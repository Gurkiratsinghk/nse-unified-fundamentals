"""
Data Source company page scraper - Orchestration logic.

Delegates HTML parsing to `parsers.py` and Cloudflare bypass/fetching to `bypass.py`.
"""

import sys
from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from data.db import get_db
from data.models import Company, CompanyEssentials, YearlyFinancial, QuarterlyFinancial
from data.supabase_sync import sync_company_to_supabase

from scrapers.parsers import parse_company_page
from scrapers.bypass import (
    init_cloudflare_bypass,
    fetch_page,
    jitter_sleep,
    set_referer,
    COOKIE_REFRESH_INTERVAL,
    CloudflareBlockError
)

# ---------------------------------------------------------------------------
# Database writers
# ---------------------------------------------------------------------------

def _save_essentials(
    session: Session,
    symbol: str,
    data: dict[str, tuple[float | None, str | None]],
) -> None:
    """Upsert company essentials — always reflects the most recent snapshot."""
    if not data:
        return

    for metric_name, (value_num, value_text) in data.items():
        existing = (
            session.query(CompanyEssentials)
            .filter_by(symbol=symbol, metric_name=metric_name)
            .first()
        )
        if existing:
            if existing.value_num != value_num or existing.value_text != value_text:
                existing.value_num  = value_num
                existing.value_text = value_text
                existing.is_synced   = False
        else:
            session.add(CompanyEssentials(
                symbol      = symbol,
                metric_name = metric_name,
                value_num   = value_num,
                value_text  = value_text,
                is_synced   = False
            ))

    logger.debug(f"  Essentials: {len(data)} metrics upserted.")

def _save_yearly(
    session:      Session,
    symbol:       str,
    source_table: str,
    data:         dict[int, dict[str, tuple[float | None, str | None]]],
) -> tuple[int, int]:
    """Insert-only yearly financial rows."""
    inserted = 0
    skipped  = 0

    for fiscal_year, metrics in data.items():
        for metric_name, (value_num, value_text) in metrics.items():
            exists = (
                session.query(YearlyFinancial)
                .filter_by(
                    symbol       = symbol,
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
                symbol       = symbol,
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
    symbol:       str,
    source_table: str,
    data:         dict[str, dict[str, tuple[float | None, str | None]]],
) -> tuple[int, int]:
    """Insert-only quarterly financial rows."""
    inserted = 0
    skipped  = 0

    for quarter_date, metrics in data.items():
        for metric_name, (value_num, value_text) in metrics.items():
            exists = (
                session.query(QuarterlyFinancial)
                .filter_by(
                    symbol       = symbol,
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
                symbol       = symbol,
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
    """Fetch and parse the Data Source page for `symbol`."""
    try:
        soup = fetch_page(symbol)
    except CloudflareBlockError:
        raise
        
    if soup is None:
        return {}

    logger.info(f"  [{symbol}] Page fetched — parsing...")
    return parse_company_page(soup)

def save_company_fundamentals(symbol: str, session: Session) -> bool:
    """Scrape and persist all fundamentals for a single company."""
    company: Company | None = session.query(Company).filter_by(symbol=symbol).first()
    if not company:
        logger.warning(f"[{symbol}] Not in companies table — skipping.")
        return False

    logger.info(f"[{symbol}] Scraping Data Source...")
    try:
        page_data = scrape_company(symbol)
    except CloudflareBlockError:
        raise
        
    if not page_data:
        logger.error(f"[{symbol}] No data returned from scraper.")
        return False

    _save_essentials(session, symbol, page_data.get("essentials", {}))

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
            session, symbol, source_label, page_data.get(source_label, {})
        )
        total_inserted += ins
        total_skipped  += skp

    ins, skp = _save_quarterly(
        session, symbol, "quarterly_results", page_data.get("quarterly_results", {})
    )
    total_inserted += ins
    total_skipped  += skp

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        logger.error(f"[{symbol}] DB integrity error on commit: {exc}")
        return False

    sync_company_to_supabase(session, symbol)

    logger.success(
        f"[{symbol}] Done — {total_inserted} rows inserted, "
        f"{total_skipped} existing rows skipped."
    )
    return True

def run_scraper_for_symbols(symbols: list[str], callback=None) -> tuple[int, int]:
    """
    Loop through a list of symbols, scraping and saving each.
    Handles Cloudflare initialization, cookie refreshing, and 403 retries.
    """
    success = 0
    failure = 0

    if not symbols:
        return 0, 0

    if not init_cloudflare_bypass():
        logger.critical("Cloudflare bypass initialization failed. Aborting scrape to preserve data.")
        return 0, len(symbols)

    logger.info("Testing bypass connection on TCS...")
    try:
        test_soup = fetch_page("TCS")
        if not test_soup or not test_soup.find("div", id="companyessentials"):
            raise ValueError("Page returned 200 but 'companyessentials' div missing — possible WAF silent block.")
        logger.success("Connection test passed. Starting full scrape.")
    except CloudflareBlockError:
        logger.critical("Connection test failed due to 403 Forbidden.")
        return 0, len(symbols)
    except Exception as e:
        logger.critical(f"Connection test failed: {e}")
        return 0, len(symbols)

    idx = 0
    symbol_retries = 0
    while idx < len(symbols):
        symbol = symbols[idx]
        
        # Periodic cookie refresh every COOKIE_REFRESH_INTERVAL requests.
        if idx > 0 and idx % COOKIE_REFRESH_INTERVAL == 0:
            logger.info(f"[{idx+1}/{len(symbols)}] Cookie refresh interval reached. Re-solving challenge...")
            if not init_cloudflare_bypass():
                logger.critical("Cookie refresh failed. Stopping scrape early to preserve data.")
                failure += len(symbols) - idx
                break
            logger.success(f"Cookie refreshed. Resuming from [{idx+1}/{len(symbols)}].")

        if idx > 0:
            set_referer(symbols[idx - 1])

        logger.info(f"--- [{idx+1}/{len(symbols)}] {symbol} ---")
        try:
            with get_db() as session:
                ok = save_company_fundamentals(symbol, session)
            if ok:
                success += 1
            else:
                failure += 1

            if callback:
                callback(symbol, ok)
            
            # Successfully processed or moved on, reset symbol retry counter
            idx += 1
            symbol_retries = 0

        except CloudflareBlockError:
            if symbol_retries >= 1:
                logger.error(f"[{symbol}] Still receiving 403 after bypass reset. Skipping to prevent infinite loop.")
                failure += 1
                if callback:
                    callback(symbol, False)
                idx += 1
                symbol_retries = 0
                continue

            logger.warning(f"[{symbol}] 403 Forbidden encountered! Pausing to reset bypass...")
            symbol_retries += 1
            if not init_cloudflare_bypass():
                logger.critical("Failed to reset bypass after 403 error. Aborting scrape.")
                failure += len(symbols) - idx
                break
            logger.success("Bypass reset successfully. Retrying current symbol (Attempt 2)...")
            # Do NOT increment idx, we want to retry the same symbol once
            
        except Exception as exc:
            logger.error(f"[{symbol}] Unhandled exception: {exc}")
            failure += 1
            if callback:
                callback(symbol, False)
            idx += 1
            symbol_retries = 0

        if idx < len(symbols):
            jitter_sleep()

    return success, failure