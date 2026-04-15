"""
NSE100 constituent list scraper.

Fetches the current NIFTY 100 list from NSE India's API and keeps the
nse100_constituents table in sync, maintaining a full historical audit trail
of when each company was added to or removed from the index.

API field mapping (confirmed from ABB sample output):
    Top-level item keys: symbol, series, lastPrice, yearHigh, yearLow,
                         perChange365d, perChange30d, pChange, ...
    item["meta"] keys  : symbol, companyName, industry, isin, listingDate,
                         isFNOSec, isSLBSec, activeSeries, segment, ...

ISIN lives inside item["meta"]["isin"] and is stored in the companies table.

Removal policy:
    When a company is removed from the NSE100 index, its removed_date is set
    in nse100_constituents — but the company record itself is kept and its
    fundamentals continue to be scraped on every run. This preserves full
    historical coverage and avoids data gaps for ex-constituents.
"""

from datetime import date

import requests
from loguru import logger
from sqlalchemy.orm import Session

from data.db import get_db
from data.models import Company, NSE100Constituent

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NSE_HOMEPAGE = "https://www.nseindia.com"
_NSE_API_URL  = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20100"

# Minimal headers confirmed working in unit test.
# The homepage cookie handshake is mandatory — NSE blocks direct API calls
# without a valid session cookie obtained from a prior homepage visit.
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_nse100_symbols() -> list[dict]:
    """
    Call the NSE API and return a list of enriched company dicts.

    API structure confirmed from ABB sample:
        payload["data"] -> list of item objects
        item["meta"]["symbol"]       -> NSE ticker
        item["meta"]["companyName"]  -> full company name
        item["meta"]["industry"]     -> industry classification
        item["meta"]["isin"]         -> ISIN code  (e.g. "INE117A01022")
        item["meta"]["listingDate"]  -> listing date string
        item["meta"]["isFNOSec"]     -> F&O eligibility flag
        item["lastPrice"]            -> current market price
        item["yearHigh"]             -> 52-week high
        item["yearLow"]              -> 52-week low
        item["perChange365d"]        -> 1-year % change
        item["perChange30d"]         -> 30-day % change

    Returns an empty list on any network or parse failure.
    """
    try:
        http = requests.Session()
        http.headers.update(_HEADERS)

        # Step 1 — cookie handshake: NSE blocks direct API calls without this
        logger.debug(f"Fetching NSE homepage for cookie handshake: {_NSE_HOMEPAGE}")
        http.get(_NSE_HOMEPAGE, timeout=15)

        # Step 2 — API call
        logger.debug(f"Calling NSE API: {_NSE_API_URL}")
        response = http.get(_NSE_API_URL, timeout=20)
        response.raise_for_status()

        # Guard: NSE sometimes returns HTML on auth failure instead of JSON
        content_type = response.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            logger.error(
                f"NSE API did not return JSON — Content-Type: {content_type}. "
                f"Response preview: {response.text[:300]}"
            )
            return []

        payload  = response.json()
        raw_list = payload.get("data", [])

        if not raw_list:
            logger.warning("NSE API returned an empty 'data' list.")
            return []

        companies = []
        skipped   = 0

        for item in raw_list:
            meta = item.get("meta", {})

            symbol = meta.get("symbol", "").strip()
            if not symbol:
                skipped += 1
                continue

            # Capture all useful fields from both meta and top-level
            active_series = meta.get("activeSeries", [])

            companies.append({
                # Identity
                "symbol":        symbol,
                "company_name":  meta.get("companyName", "").strip(),
                "industry":      meta.get("industry", "").strip(),
                "isin":          meta.get("isin", "").strip(),
                # Listing metadata
                "listing_date":  meta.get("listingDate", "").strip(),
                "series":        ",".join(active_series) if active_series else "",
                "is_fno":        meta.get("isFNOSec", False),
                "segment":       meta.get("segment", "").strip(),
                # Current market snapshot (informational — not stored in DB,
                # but logged for diagnostics)
                "last_price":    item.get("lastPrice"),
                "year_high":     item.get("yearHigh"),
                "year_low":      item.get("yearLow"),
                "pct_chg_365d":  item.get("perChange365d"),
                "pct_chg_30d":   item.get("perChange30d"),
            })

        logger.info(
            f"NSE API: {len(companies)} valid symbols extracted "
            f"({skipped} items skipped — missing symbol in meta)."
        )
        return companies

    except requests.exceptions.HTTPError as exc:
        logger.error(f"NSE API HTTP error: {exc} — Status: {exc.response.status_code}")
        return []
    except requests.exceptions.RequestException as exc:
        logger.error(f"NSE API request failed: {exc}")
        return []
    except Exception as exc:
        logger.error(f"Unexpected error fetching NSE100 list: {exc}")
        return []


def _get_or_create_company(session: Session, entry: dict) -> Company:
    """
    Return an existing Company row or insert a new one.

    On subsequent runs, updates company_name, industry, and isin in case
    NSE has changed them (e.g. renames, reclassifications, ISIN corrections).
    """
    symbol = entry["symbol"]
    company = session.query(Company).filter_by(symbol=symbol).first()

    if not company:
        company = Company(
            symbol       = symbol,
            company_name = entry["company_name"] or None,
            industry     = entry["industry"]      or None,
            isin         = entry["isin"]          or None,
        )
        session.add(company)
        session.flush()  # populate company.id without a full commit
        logger.info(
            f"New company inserted: {symbol} — {entry['company_name']} "
            f"(ISIN: {entry['isin'] or 'N/A'})"
        )
    else:
        # Update mutable fields — names and classifications can change
        changed = []
        if entry["company_name"] and company.company_name != entry["company_name"]:
            company.company_name = entry["company_name"]
            changed.append("company_name")
        if entry["industry"] and company.industry != entry["industry"]:
            company.industry = entry["industry"]
            changed.append("industry")
        if entry["isin"] and company.isin != entry["isin"]:
            company.isin = entry["isin"]
            changed.append("isin")
        if changed:
            logger.debug(f"Updated {symbol}: {', '.join(changed)}")

    return company


def _get_active_constituent(session: Session, company_id: int) -> NSE100Constituent | None:
    """Return the currently active constituent row for a company, or None."""
    return (
        session.query(NSE100Constituent)
        .filter_by(company_id=company_id, removed_date=None)
        .first()
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_and_update_nse100() -> None:
    """
    Sync the nse100_constituents table against the live NSE100 index.

    Steps
    -----
    1. Fetch current NSE100 list from NSE India's API.
    2. For each company in the fresh list:
       a. Insert into `companies` if not already present (or update fields).
       b. Insert a new active row in `nse100_constituents` if not already active.
    3. For any company currently active in the DB but absent from the fresh list:
       - Set removed_date = today in `nse100_constituents`.
       - The company record is kept; its fundamentals continue to be scraped.

    Removal policy
    --------------
    Removal only affects the constituent status flag. The company remains in
    the `companies` table and continues to appear in Data Source scrape runs.
    This ensures no historical data gaps for ex-index members.
    """
    logger.info("=== Starting NSE100 list update ===")

    fresh_list = _fetch_nse100_symbols()
    if not fresh_list:
        logger.warning("No data returned from NSE API — aborting list update.")
        return

    fresh_symbols = {entry["symbol"] for entry in fresh_list}
    today         = date.today()

    with get_db() as session:

        # ---- Step 1 & 2: sync fresh symbols into DB ----
        for entry in fresh_list:
            company    = _get_or_create_company(session, entry)
            active_row = _get_active_constituent(session, company.id)

            if not active_row:
                session.add(NSE100Constituent(
                    company_id = company.id,
                    added_date = today,
                ))
                logger.info(
                    f"Added to NSE100: {entry['symbol']} "
                    f"(added_date={today}, price={entry['last_price']})"
                )

        # ---- Step 3: mark removals — constituent flag only ----
        active_rows = (
            session.query(NSE100Constituent)
            .filter(NSE100Constituent.removed_date.is_(None))
            .all()
        )

        removals = 0
        for row in active_rows:
            co = session.get(Company, row.company_id)
            if co and co.symbol not in fresh_symbols:
                row.removed_date = today
                removals += 1
                logger.info(
                    f"Removed from NSE100: {co.symbol} (removed_date={today}) "
                    f"— fundamentals scraping will continue."
                )

        session.commit()

    logger.success(
        f"=== NSE100 list update complete — "
        f"{len(fresh_list)} active, {removals} newly removed ==="
    )


def get_all_scrape_symbols() -> list[str]:
    """
    Return symbols for ALL companies ever seen in the NSE100 — both currently
    active and previously removed.

    This is the correct list to pass to the Data Source scraper so that removed
    companies continue to have their fundamentals tracked.
    """
    with get_db() as session:
        rows = (
            session.query(Company.symbol)
            .join(NSE100Constituent, NSE100Constituent.company_id == Company.id)
            .distinct()
            .order_by(Company.symbol)
            .all()
        )
    return [row.symbol for row in rows]


def get_active_symbols() -> list[str]:
    """
    Return symbols for companies currently active in the NSE100 only.
    Useful for reporting or filtering, but NOT for the scrape pipeline.
    """
    with get_db() as session:
        rows = (
            session.query(Company.symbol)
            .join(NSE100Constituent, NSE100Constituent.company_id == Company.id)
            .filter(NSE100Constituent.removed_date.is_(None))
            .order_by(Company.symbol)
            .all()
        )
    return [row.symbol for row in rows]