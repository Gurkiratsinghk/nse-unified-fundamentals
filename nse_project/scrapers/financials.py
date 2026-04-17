"""
Data Source company page scraper.

For each symbol it:
  1. Fetches https://ticker.finology.in/company/{SYMBOL}
  2. Parses the #companyessentials block — targeted <small>/<p> approach
     confirmed by unit testing (unit.py)
  3. Parses the Profit & Loss (table index 2), Balance Sheet (index 3),
     and Cash Flow (index 4) yearly tables using confirmed table positions
  4. Parses Promoter Shareholding (index 5) and Investor Shareholding (index 6)
  5. Stores all data in the database — essentials are upserted (current snapshot),
     yearly data is insert-only (historical preservation)

Value normalisation rules (confirmed from unit.py output):
  - All "Cr." suffixes are stripped; values stored as plain floats (crores)
  - "%" values are stored as plain floats (e.g. 0.66, not 0.0066)
    The metric name itself signals it is a percentage
  - Commas, Rs, whitespace, and non-breaking spaces are stripped
  - Dash "-" and blank cells are stored as NULL
  - Values that cannot be parsed to float are stored as value_text

Table index mapping (0-based, confirmed from unit.py page inspection):
  Index 0 : header/summary table — skipped
  Index 1 : header/summary table — skipped
  Index 2 : Annual Income Statement (P&L)
  Index 3 : Balance Sheet
  Index 4 : Cash Flow Statement
  Index 5 : Promoter Shareholding
  Index 6 : Investor Shareholding
"""

import re
import time
import sys

import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests
from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from data.db import get_db
from data.models import Company, CompanyEssentials, YearlyFinancial, QuarterlyFinancial
from data.supabase_sync import sync_company_to_supabase

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL  = "https://ticker.finology.in/company/"
_SLEEP_SEC = 2
_TIMEOUT   = 20

# NOTE: Do NOT set User-Agent or Accept-Language here.
# curl_cffi's impersonate="chrome" generates headers that match its TLS
# fingerprint. Overriding them creates a detectable mismatch that triggers WAFs.
_EXTRA_HEADERS = {
    "Referer": "https://ticker.finology.in/",
}

# Table positions on the Data Source page (0-based), confirmed via unit.py
_TABLE_IDX_QUARTERLY  = 1
_TABLE_IDX_PL         = 2
_TABLE_IDX_BS         = 3
_TABLE_IDX_CF         = 4
_TABLE_IDX_PROMOTER   = 5
_TABLE_IDX_INVESTOR   = 6


# ---------------------------------------------------------------------------
# Shared parsing utilities
# ---------------------------------------------------------------------------

def _to_snake(text: str) -> str:
    """
    Convert a human-readable label to a clean snake_case key.
    e.g. "Net Profit Margin (%)" -> "net_profit_margin_pct"
         "P/E"                   -> "p_e"
         "Book Value (TTM)"      -> "book_value_ttm"
    """
    text = text.strip()
    text = text.replace("%", "pct")
    text = re.sub(r"[^\w\s]", " ", text)       # non-alphanumeric -> space
    text = re.sub(r"\s+", "_", text.strip()).lower()
    text = re.sub(r"_+", "_", text).strip("_") # collapse repeated underscores
    return text or "unknown"


def _clean_numeric_string(raw: str) -> str:
    """
    Strip all unit suffixes and formatting from a raw string, leaving only
    a number string suitable for float() conversion.

    Strips: commas, Rs symbol, 'Cr.', 'Cr', '%', whitespace, non-breaking space.
    Does NOT strip the leading minus sign or decimal point.
    """
    return (
        raw.strip()
           .replace("\xa0", " ")
           .replace(",", "")
           .replace("\u20b9", "")   # rupee sign
           .replace("Rs", "")
           .replace("Cr.", "")
           .replace("Cr",  "")
           .replace("%",   "")
           .strip()
    )


def _parse_value(
    raw: str,
    default_to_crores: bool = False,
    default_to_percentage: bool = False,
    metric_key: str = ""
) -> tuple[float | None, str | None]:
    """
    Parse a raw cell/field string into (value_num, value_text).

    Returns
    -------
    (float, None)  -- successfully parsed to a number
    (None, str)    -- non-numeric text worth preserving (e.g. dates, names)
    (None, None)   -- empty / null marker (dash, blank, N/A)

    Rules
    -----
    - "Cr." suffix is stripped; if detected or flag passed, value is multiplied by 10,000,000.
    - "%" suffix is stripped; if detected or flag passed, value is divided by 100. 
      The metric name signals the unit.
    - "-" or "--" are treated as NULL, not zero.
    """
    if not raw:
        return None, None

    cleaned = _clean_numeric_string(raw)

    if cleaned in ("", "-", "--", "N/A", "NA", "n/a"):
        return None, None

    try:
        val = float(cleaned)
    except ValueError:
        return None, cleaned if cleaned else None
        
    is_crores = "Cr" in raw
    is_percentage = "%" in raw or "pct" in metric_key

    if default_to_crores and not is_crores:
        skip_scaling = (is_percentage or "rs" in metric_key)
        if not skip_scaling:
            is_crores = True

    if default_to_percentage and not is_percentage:
        is_percentage = True

    if is_crores:
        val *= 10000000.0
    elif is_percentage:
        val /= 100.0

    return val, None


# ---------------------------------------------------------------------------
# Page fetcher
# ---------------------------------------------------------------------------

import random

# Use curl_cffi session which impersonates Chrome's TLS fingerprint (JA3)
# to bypass TLS-fingerprint-based bot detection on datacenter IPs.
_SESSION = curl_requests.Session(impersonate="chrome")


def _jitter_sleep(base_sec: float = _SLEEP_SEC):
    """Sleep with random jitter to avoid bot detection."""
    jitter = random.uniform(0.5, 1.5)
    time.sleep(base_sec * jitter)


# #4: Hardcoded mapping for problematic symbols as a last-resort safeguard
_SPECIAL_CASES = {
    "M&M":    "SCRIP-100520",
    "L&TFH":  "SCRIP-105164",
    "M&MFIN": "SCRIP-105212",
    "ARE&M": "SCRIP-100008",
    "GMRP&UI": "SCRIP-305653",
    "GVT&D": "SCRIP-122275",
    "J&KBANK": "SCRIP-132209",
    "SURANAT&P": "SCRIP-117530",
}


def _fetch_page(symbol: str) -> BeautifulSoup | None:
    """
    Fetch and parse the Data Source page for a given symbol.

    Handles special cases:
    - Symbols with special characters (M&M → URL-encoded as M%26M)
    - Data Source redirects (some symbols redirect to SCRIP-XXXXX URLs)
    - Search fallback: if direct URL fails, searches Data Source for the symbol
    """
    from urllib.parse import quote

    # #4: Try hardcoded special cases first
    if symbol in _SPECIAL_CASES:
        scrip_url = f"{_BASE_URL}{_SPECIAL_CASES[symbol]}"
        try:
            resp = _SESSION.get(scrip_url, headers=_EXTRA_HEADERS, timeout=_TIMEOUT, allow_redirects=True)
            resp.raise_for_status()
            logger.info(f"  [{symbol}] Resolved via special case mapping to: {scrip_url}")
            return BeautifulSoup(resp.text, "lxml")
        except curl_requests.RequestsError as exc:
            logger.warning(f"[{symbol}] Special case mapping failed: {exc}")

    # Try 1: Direct URL with proper encoding of the symbol
    encoded_symbol = quote(symbol, safe="")
    url = f"{_BASE_URL}{encoded_symbol}"
    try:
        response = _SESSION.get(url, headers=_EXTRA_HEADERS, timeout=_TIMEOUT, allow_redirects=True)
        response.raise_for_status()
        # Log if Data Source redirected us (e.g. M&M → SCRIP-100520)
        if response.url != url:
            logger.info(f"  [{symbol}] Redirected to: {response.url}")
        return BeautifulSoup(response.text, "lxml")
    except curl_requests.RequestsError as exc:
        status = getattr(getattr(exc, 'response', None), 'status_code', None)
        if status in (400, 404):
            logger.debug(f"[{symbol}] Direct URL failed with {status}, trying search fallback...")
        elif status == 403:
            logger.warning(f"[{symbol}] HTTP error {status}: {exc}")
            return None
        else:
            logger.error(f"[{symbol}] Request failed: {exc}")
            return None

    # Try 2: Search Data Source for the symbol
    # #4: Refined search query for special characters
    search_query = encoded_symbol
    success_soup = None
    
    for query in [search_query, quote(symbol.replace("&", " "), safe="")]:
        search_url = f"https://ticker.finology.in/GetSearchData.ashx?q={query}"
        try:
            search_resp = _SESSION.get(search_url, headers=_EXTRA_HEADERS, timeout=_TIMEOUT)
            search_resp.raise_for_status()
            results = search_resp.json()
            if results and isinstance(results, list) and len(results) > 0:
                first = results[0]
                fincode = first.get("FINCODE")
                if fincode:
                    resolved_url = f"{_BASE_URL}SCRIP-{fincode}"
                    logger.info(f"  [{symbol}] Search ({query}) resolved via FINCODE to: {resolved_url}")
                    resp2 = _SESSION.get(resolved_url, headers=_EXTRA_HEADERS, timeout=_TIMEOUT, allow_redirects=True)
                    resp2.raise_for_status()
                    success_soup = BeautifulSoup(resp2.text, "lxml")
                    break
        except curl_requests.RequestsError as exc:
            logger.debug(f"[{symbol}] Search fallback for '{query}' failed: {exc}")
            
    if success_soup:
        return success_soup

    logger.warning(f"[{symbol}] Could not resolve Data Source page.")
    return None


# ---------------------------------------------------------------------------
# Company essentials parser
#
# Confirmed approach from unit.py:
#   block = soup.find('div', id='companyessentials')
#   containers = block.find_all('div', class_=lambda x: 'col-' in x or 'mb-' in x)
#   label = container.find('small').get_text()
#   value = container.find('p').get_text()
# ---------------------------------------------------------------------------

def _is_numeric_like(text: str) -> bool:
    """Check if a string looks like a numeric value (e.g. '123_45', '123.45', '123')."""
    # Remove chars we often strip during numeric parsing to see if what's left is a number
    cleaned = text.replace("_", "").replace(".", "").replace(",", "").strip()
    return cleaned.isdigit()


def _parse_essentials(soup: BeautifulSoup) -> dict[str, tuple[float | None, str | None]]:
    """
    Extract every labeled metric from the #companyessentials div.

    Data Source HTML structure (confirmed):
        <div id="companyessentials">
            <div class="col-6 col-md-3 mb-2">
                <small>Market Cap</small>
                <p>5507.82 Cr.</p>
            </div>
            ...
        </div>

    All values are cleaned and stored as plain floats where possible.
    Returns: { snake_case_label -> (value_num, value_text) }
    """
    results: dict[str, tuple[float | None, str | None]] = {}

    block = soup.find("div", id="companyessentials")
    if not block:
        logger.warning("  #companyessentials div not found on page.")
        return results

    containers = block.find_all(
        "div",
        class_=lambda cls: cls and ("col-" in cls or "mb-" in cls)
    )

    for div in containers:
        label_tag = div.find("small")
        value_tag = div.find("p")

        if not label_tag or not value_tag:
            continue

        label = label_tag.get_text(strip=True)
        value = value_tag.get_text(strip=True)

        # CRITICAL FIX: Skip labels that look like numbers (e.g. '126869_54')
        # Data Source sometimes puts numeric data in the <small> tag improperly.
        if not label or _is_numeric_like(label):
            if label:
                logger.debug(f"  Skipping numeric-like label: '{label}'")
            continue

        # Skip values that are blank or reduce to nothing after stripping units
        if not value or not _clean_numeric_string(value):
            continue

        key = _to_snake(label)
        if key in results:
            continue  # first occurrence wins

        num, txt = _parse_value(value, metric_key=key)
        results[key] = (num, txt if num is None else None)

    logger.debug(f"  Essentials: {len(results)} clean metrics extracted.")
    return results


# ---------------------------------------------------------------------------
# Financial table parsers
# ---------------------------------------------------------------------------

def _extract_year_headers(header_cells: list) -> list[int | None]:
    """
    Parse a list of <th>/<td> BeautifulSoup elements into fiscal years.
    "Mar 2021", "FY2021", "2021" -> 2021. Unknown/TTM headers -> None.
    """
    years: list[int | None] = []
    for cell in header_cells:
        text  = cell.get_text(strip=True)
        match = re.search(r"\b(20\d{2}|19\d{2})\b", text)
        years.append(int(match.group()) if match else None)
    return years


def _parse_table(
    table_soup: BeautifulSoup,
    source_label: str,
    known_year_headers: list[int | None] | None = None,
) -> dict[int, dict[str, tuple[float | None, str | None]]]:
    """
    Parse a financial data table into a year-keyed results dict.

    Parameters
    ----------
    table_soup          : BeautifulSoup <table> element
    source_label        : descriptive name used in log messages
    known_year_headers  : year list from the P&L table — required for Balance
                          Sheet because its own header row contains blank cells

    Returns
    -------
    { fiscal_year (int) -> { snake_case_metric -> (value_num, value_text) } }
    """
    result: dict[int, dict[str, tuple[float | None, str | None]]] = {}
    is_financial_statement = source_label in ("profit_loss", "balance_sheet", "cash_flow")
    is_shareholding = source_label in ("promoter_shareholding", "investor_shareholding")

    all_rows = table_soup.find_all("tr")
    if not all_rows:
        logger.warning(f"  Table '{source_label}': no rows found.")
        return result

    # ---- Determine year columns ----
    header_cells = all_rows[0].find_all(["th", "td"])
    header_texts = [c.get_text(strip=True) for c in header_cells]

    # Balance Sheet pattern: first row is all-blank except for column 0
    # In this case use the year list extracted from the P&L table
    all_blank_after_first = (
        len(header_texts) > 1 and all(h == "" for h in header_texts[1:])
    )

    if all_blank_after_first and known_year_headers:
        year_columns = known_year_headers
    else:
        # Skip column 0 (row-label column), parse the rest
        year_columns = _extract_year_headers(header_cells[1:])

    if not any(year_columns):
        logger.warning(f"  Table '{source_label}': no fiscal years resolved from headers.")
        return result

    # ---- Parse data rows (skip header row) ----
    for row in all_rows[1:]:
        cells = row.find_all(["th", "td"])
        if not cells:
            continue

        metric_raw = cells[0].get_text(strip=True)
        if not metric_raw:
            continue

        metric_key = _to_snake(metric_raw)

        for col_idx, fiscal_year in enumerate(year_columns):
            if fiscal_year is None:
                continue

            cell_idx = col_idx + 1  # offset: col 0 is always the metric label
            if cell_idx >= len(cells):
                break

            raw_value = cells[cell_idx].get_text(strip=True)
            num, txt  = _parse_value(
                raw_value, 
                default_to_crores=is_financial_statement, 
                default_to_percentage=is_shareholding,
                metric_key=metric_key
            )

            result.setdefault(fiscal_year, {})[metric_key] = (num, txt)

    metrics_sample = len(next(iter(result.values()), {}))
    logger.debug(
        f"  Table '{source_label}': "
        f"{len(result)} years x {metrics_sample} metrics"
    )
    return result


def _parse_quarterly_table(
    table_soup: BeautifulSoup,
    source_label: str = "quarterly_results",
) -> dict[str, dict[str, tuple[float | None, str | None]]]:
    """
    Parse a quarterly financial data table.
    Returns: { quarter_date (str) -> { snake_case_metric -> (value_num, value_text) } }
    """
    result: dict[str, dict[str, tuple[float | None, str | None]]] = {}
    
    all_rows = table_soup.find_all("tr")
    if not all_rows:
        logger.warning(f"  Table '{source_label}': no rows found.")
        return result

    header_cells = all_rows[0].find_all(["th", "td"])
    quarter_columns = [c.get_text(strip=True) for c in header_cells[1:]]

    for row in all_rows[1:]:
        cells = row.find_all(["th", "td"])
        if not cells:
            continue

        metric_raw = cells[0].get_text(strip=True)
        if not metric_raw:
            continue

        metric_key = _to_snake(metric_raw)

        for col_idx, quarter_str in enumerate(quarter_columns):
            if not quarter_str or quarter_str.upper() == "TTM":
                continue

            cell_idx = col_idx + 1
            if cell_idx >= len(cells):
                break

            raw_value = cells[cell_idx].get_text(strip=True)
            num, txt = _parse_value(
                raw_value, 
                default_to_crores=True, 
                metric_key=metric_key
            )

            result.setdefault(quarter_str, {})[metric_key] = (num, txt)

    metrics_sample = len(next(iter(result.values()), {}))
    logger.debug(
        f"  Table '{source_label}': "
        f"{len(result)} quarters x {metrics_sample} metrics"
    )
    return result


def _parse_all_tables(
    soup: BeautifulSoup,
) -> dict[str, dict[int, dict[str, tuple[float | None, str | None]]]]:
    """
    Extract all financial tables from the page using confirmed index positions.

    The P&L table is always parsed first to obtain the canonical year headers,
    which are then passed to the Balance Sheet parser (its own header row is blank).
    """
    tables = soup.find_all("table")
    total  = len(tables)
    logger.debug(f"  Page contains {total} tables.")

    output: dict[str, dict] = {}

    def _get(idx: int, label: str) -> BeautifulSoup | None:
        if idx >= total:
            logger.warning(
                f"  Table '{label}' expected at index {idx} "
                f"but page only has {total} tables — skipping."
            )
            return None
        return tables[idx]

    # Quarterly Results
    q_table = _get(_TABLE_IDX_QUARTERLY, "quarterly_results")
    if q_table:
        output["quarterly_results"] = _parse_quarterly_table(q_table, "quarterly_results")
    else:
        output["quarterly_results"] = {}

    # P&L first — needed to derive canonical year header list
    pl_table = _get(_TABLE_IDX_PL, "profit_loss")
    if pl_table:
        output["profit_loss"] = _parse_table(pl_table, "profit_loss")
        pl_header_row   = pl_table.find_all("tr")[0].find_all(["th", "td"])
        year_header_list = _extract_year_headers(pl_header_row[1:])
    else:
        output["profit_loss"] = {}
        year_header_list       = []

    # Balance Sheet — pass year headers from P&L
    bs_table = _get(_TABLE_IDX_BS, "balance_sheet")
    output["balance_sheet"] = (
        _parse_table(bs_table, "balance_sheet", known_year_headers=year_header_list)
        if bs_table else {}
    )

    # Cash Flow
    cf_table = _get(_TABLE_IDX_CF, "cash_flow")
    output["cash_flow"] = (
        _parse_table(cf_table, "cash_flow", known_year_headers=year_header_list)
        if cf_table else {}
    )

    # Promoter Shareholding
    pr_table = _get(_TABLE_IDX_PROMOTER, "promoter_shareholding")
    output["promoter_shareholding"] = (
        _parse_table(pr_table, "promoter_shareholding")
        if pr_table else {}
    )

    # Investor Shareholding
    inv_table = _get(_TABLE_IDX_INVESTOR, "investor_shareholding")
    output["investor_shareholding"] = (
        _parse_table(inv_table, "investor_shareholding")
        if inv_table else {}
    )

    return output


# ---------------------------------------------------------------------------
# Database writers
# ---------------------------------------------------------------------------

def _save_essentials(
    session: Session,
    company_id: int,
    data: dict[str, tuple[float | None, str | None]],
) -> None:
    """
    Upsert company essentials — always reflects the most recent snapshot.
    Overwrites previous values on each run.
    """
    if not data:
        return

    for metric_name, (value_num, value_text) in data.items():
        existing = (
            session.query(CompanyEssentials)
            .filter_by(company_id=company_id, metric_name=metric_name)
            .first()
        )
        if existing:
            # #3: Reset is_synced=False if value has changed
            if existing.value_num != value_num or existing.value_text != value_text:
                existing.value_num  = value_num
                existing.value_text = value_text
                existing.is_synced   = False
        else:
            session.add(CompanyEssentials(
                company_id  = company_id,
                metric_name = metric_name,
                value_num   = value_num,
                value_text  = value_text,
                is_synced   = False
            ))

    logger.debug(f"  Essentials: {len(data)} metrics upserted.")


def _save_yearly(
    session:      Session,
    company_id:   int,
    source_table: str,
    data:         dict[int, dict[str, tuple[float | None, str | None]]],
) -> tuple[int, int]:
    """
    Insert-only yearly financial rows.

    Skips any (company_id, fiscal_year, source_table, metric_name) that already
    exists — preserving historical data across runs as new years accumulate.

    Returns (inserted_count, skipped_count).
    """
    inserted = 0
    skipped  = 0

    for fiscal_year, metrics in data.items():
        for metric_name, (value_num, value_text) in metrics.items():
            exists = (
                session.query(YearlyFinancial)
                .filter_by(
                    company_id   = company_id,
                    fiscal_year  = fiscal_year,
                    source_table = source_table,
                    metric_name  = metric_name,
                )
                .first()
            )
            if exists:
                skipped += 1
                continue

            session.add(YearlyFinancial(
                company_id   = company_id,
                fiscal_year  = fiscal_year,
                source_table = source_table,
                metric_name  = metric_name,
                value_num    = value_num,
                value_text   = value_text,
            ))
            inserted += 1

    return inserted, skipped


def _save_quarterly(
    session:      Session,
    company_id:   int,
    source_table: str,
    data:         dict[str, dict[str, tuple[float | None, str | None]]],
) -> tuple[int, int]:
    """
    Insert-only quarterly financial rows.
    Returns (inserted_count, skipped_count).
    """
    inserted = 0
    skipped  = 0

    for quarter_date, metrics in data.items():
        for metric_name, (value_num, value_text) in metrics.items():
            exists = (
                session.query(QuarterlyFinancial)
                .filter_by(
                    company_id   = company_id,
                    quarter_date = quarter_date,
                    source_table = source_table,
                    metric_name  = metric_name,
                )
                .first()
            )
            if exists:
                skipped += 1
                continue

            session.add(QuarterlyFinancial(
                company_id   = company_id,
                quarter_date = quarter_date,
                source_table = source_table,
                metric_name  = metric_name,
                value_num    = value_num,
                value_text   = value_text,
            ))
            inserted += 1

    return inserted, skipped


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape_company(symbol: str) -> dict:
    """
    Fetch and parse the Data Source page for `symbol`.

    Returns a dict with keys:
        essentials            : { metric -> (num, text) }
        quarterly_results     : { quarter_str -> { metric -> (num, text) } }
        profit_loss           : { year -> { metric -> (num, text) } }
        balance_sheet         : { year -> { metric -> (num, text) } }
        cash_flow             : { year -> { metric -> (num, text) } }
        promoter_shareholding : { year -> { metric -> (num, text) } }
        investor_shareholding : { year -> { metric -> (num, text) } }

    Returns empty dict on any fetch or parse failure.
    """
    soup = _fetch_page(symbol)
    if soup is None:
        return {}

    logger.info(f"  [{symbol}] Page fetched — parsing...")
    essentials = _parse_essentials(soup)
    tables     = _parse_all_tables(soup)

    return {"essentials": essentials, **tables}


def save_company_fundamentals(symbol: str, session: Session) -> bool:
    """
    Scrape and persist all fundamentals for a single company.

    - Essentials are upserted each run (always current snapshot).
    - Yearly data is insert-only (historical rows are never overwritten).
    - Returns True on success, False on any failure.
    """
    company: Company | None = session.query(Company).filter_by(symbol=symbol).first()
    if not company:
        logger.warning(f"[{symbol}] Not in companies table — skipping.")
        return False

    logger.info(f"[{symbol}] Scraping Data Source...")
    page_data = scrape_company(symbol)

    if not page_data:
        logger.error(f"[{symbol}] No data returned from scraper.")
        return False

    _save_essentials(session, company.id, page_data.get("essentials", {}))

    total_inserted = 0
    total_skipped  = 0

    for source_label in (
        "profit_loss",
        "balance_sheet",
        "cash_flow",
        "promoter_shareholding",
        "investor_shareholding",
    ):
        ins, skp = _save_yearly(
            session, company.id, source_label, page_data.get(source_label, {})
        )
        total_inserted += ins
        total_skipped  += skp

    # Save quarterly results
    ins, skp = _save_quarterly(
        session, company.id, "quarterly_results", page_data.get("quarterly_results", {})
    )
    total_inserted += ins
    total_skipped  += skp

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        logger.error(f"[{symbol}] DB integrity error on commit: {exc}")
        return False

    # Sync to Supabase after successful local commit
    sync_company_to_supabase(session, symbol)

    logger.success(
        f"[{symbol}] Done — {total_inserted} rows inserted, "
        f"{total_skipped} existing rows skipped."
    )
    return True

def _init_cloudflare_bypass() -> bool:
    """
    Uses SeleniumBase (with Xvfb on Linux) to solve the Cloudflare Turnstile
    challenge, extracts the cf_clearance cookie, and injects it into curl_cffi.
    """
    global _SESSION, _EXTRA_HEADERS
    logger.info("Initializing Cloudflare Bypass via SeleniumBase...")
    url_solve = f"{_BASE_URL}TCS"

    try:
        from seleniumbase import SB
        
        display = None
        if sys.platform.startswith('linux'):
            from pyvirtualdisplay import Display
            display = Display(visible=0, size=(1280, 720))
            display.start()

        cf_clearance = None
        user_agent = None

        with SB(uc=True, headless=False) as sb:
            sb.uc_open_with_reconnect(url_solve, 4)
            time.sleep(5)
            
            title = sb.get_title()
            if "Just a moment" in title or "Cloudflare" in title:
                logger.info("Cloudflare challenge encountered, waiting for auto-solve...")
                # Usually Turnstile auto-solves in UC mode. Don't use PyAutoGUI on headless Xvfb.
                time.sleep(10)
                title = sb.get_title()
            
            # Extract cookies and UA
            for c in sb.get_cookies():
                if c['name'] == 'cf_clearance':
                    cf_clearance = c['value']
                    break
            user_agent = sb.execute_script("return navigator.userAgent;")

        if display:
            try:
                display.stop()
            except Exception as e:
                logger.warning(f"Ignored error while stopping virtual display: {e}")

        if not cf_clearance:
            logger.error(f"Failed to get cf_clearance cookie. Final title: {title}")
            return False

        logger.success(f"Successfully obtained cf_clearance cookie.")
        
        # Inject into curl_cffi session
        _EXTRA_HEADERS["User-Agent"] = user_agent
        _SESSION.cookies.set("cf_clearance", cf_clearance, domain=".finology.in")
        return True

    except Exception:
        logger.exception("Failed to initialize SeleniumBase bypass")
        return False

def run_scraper_for_symbols(symbols: list[str], callback=None) -> tuple[int, int]:
    """
    Loop through a list of symbols, scraping and saving each.
    
    Optional 'callback' is called after each symbol with (symbol, is_success).

    Accepts any list — both active and previously removed companies —
    so the caller decides the scope. Enforces the mandatory inter-request sleep.

    Returns (success_count, failure_count).
    """
    success = 0
    failure = 0

    if not symbols:
        return 0, 0

    # 1. Initialize Bypass (extract cookie using SeleniumBase)
    if not _init_cloudflare_bypass():
        logger.critical("Cloudflare bypass initialization failed. Aborting scrape to preserve data.")
        return 0, len(symbols)

    # 2. Connection Test (make sure curl_cffi actually works with the cookie)
    logger.info("Testing connection bypass on generic page (TCS)...")
    try:
        test_resp = _SESSION.get(f"{_BASE_URL}TCS", headers=_EXTRA_HEADERS, timeout=_TIMEOUT)
        test_resp.raise_for_status()
        test_soup = BeautifulSoup(test_resp.text, "lxml")
        if not test_soup.find("div", id="companyessentials"):
            raise ValueError("Test scrape returned 200 but failed to find 'companyessentials'. Possible WAF silent block.")
        logger.success("Connection test passed! Proceeding with full scrape.")
    except Exception as e:
        logger.critical(f"Connection test failed! The bypass is not working properly. Error: {e}")
        logger.critical("Aborting full scrape to preserve database integrity.")
        return 0, len(symbols)

    for idx, symbol in enumerate(symbols, start=1):
        logger.info(f"--- [{idx}/{len(symbols)}] {symbol} ---")
        try:
            with get_db() as session:
                ok = save_company_fundamentals(symbol, session)
            if ok:
                success += 1
            else:
                failure += 1
            
            if callback:
                callback(symbol, ok)
        except Exception as exc:
            logger.error(f"[{symbol}] Unhandled exception: {exc}")
            failure += 1

        if idx < len(symbols):
            _jitter_sleep(_SLEEP_SEC)

    return success, failure