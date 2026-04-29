"""
Supabase Synchronization Module.

Handles pushing local SQLite data to Supabase with:
- Per-company sync (called after each scrape)
- Delta-sync: only pushes records where is_synced=False
- Automatic retry with exponential backoff for flaky connections
- sync_all_pending(): catches up all unsynced records (called at pipeline start/end)
"""

import time
import os
from supabase import create_client, Client
from loguru import logger
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from data.models import Company, CompanyEssentials, YearlyFinancial, QuarterlyFinancial

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        logger.error(f"Failed to initialize Supabase client: {e}")
        supabase = None
else:
    supabase = None


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------

def _execute_with_retry(query, max_retries=3, delay=5):
    """Execute a Supabase query with exponential backoff on timeout."""
    for attempt in range(max_retries):
        try:
            return query.execute()
        except Exception as e:
            is_timeout = "10060" in str(e) or "timeout" in str(e).lower() or "connect" in str(e).lower()
            if is_timeout and attempt < max_retries - 1:
                wait = delay * (2 ** attempt)
                logger.warning(f"Connection issue (attempt {attempt+1}/{max_retries}). Retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise


def _is_supabase_reachable() -> bool:
    """Quick connectivity check before attempting a full sync."""
    if not supabase:
        return False
    try:
        # Lightweight query — just check if we can talk to the API
        supabase.table("companies").select("symbol").limit(1).execute()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Per-company sync (called after each scrape)
# ---------------------------------------------------------------------------

def sync_company_to_supabase(session: Session, symbol: str) -> bool:
    """
    Syncs a single company's UNSYNCED data from local SQLite to Supabase.
    Marks records as is_synced=True on success.
    """
    if not supabase:
        return False

    company = session.query(Company).filter_by(symbol=symbol).first()
    if not company:
        return False

    chunk_size = 500
    try:
        # 1. Upsert Company record
        _execute_with_retry(
            supabase.table("companies").upsert({
                "symbol": company.symbol,
                "company_name": company.company_name,
                "sector": company.sector,
                "industry": company.industry,
                "isin": company.isin
            }, on_conflict="symbol")
        )

        # 2. Sync unsynced CompanyEssentials
        unsynced_ess = (
            session.query(CompanyEssentials)
            .filter_by(symbol=company.symbol, is_synced=False)
            .all()
        )
        if unsynced_ess:
            ess_payload = [{
                "symbol": company.symbol,
                "metric_name": e.metric_name,
                "value_num": e.value_num,
                "value_text": e.value_text
            } for e in unsynced_ess]
            _execute_with_retry(
                supabase.table("company_essentials").upsert(
                    ess_payload, on_conflict="symbol, metric_name"
                )
            )
            for e in unsynced_ess:
                e.is_synced = True

        # 3. Sync unsynced YearlyFinancials
        unsynced_yr = (
            session.query(YearlyFinancial)
            .filter_by(symbol=company.symbol, is_synced=False)
            .all()
        )
        if unsynced_yr:
            yr_payload = [{
                "symbol": company.symbol,
                "fiscal_year": y.fiscal_year,
                "source_table": y.source_table,
                "metric_name": y.metric_name,
                "value_num": y.value_num,
                "value_text": y.value_text
            } for y in unsynced_yr]
            for i in range(0, len(yr_payload), chunk_size):
                _execute_with_retry(
                    supabase.table("yearly_financials").upsert(
                        yr_payload[i:i+chunk_size],
                        on_conflict="symbol, fiscal_year, source_table, metric_name"
                    )
                )
            for y in unsynced_yr:
                y.is_synced = True

        # 4. Sync unsynced QuarterlyFinancials
        unsynced_qt = (
            session.query(QuarterlyFinancial)
            .filter_by(symbol=company.symbol, is_synced=False)
            .all()
        )
        if unsynced_qt:
            qt_payload = [{
                "symbol": company.symbol,
                "quarter_date": q.quarter_date,
                "source_table": q.source_table,
                "metric_name": q.metric_name,
                "value_num": q.value_num,
                "value_text": q.value_text
            } for q in unsynced_qt]
            for i in range(0, len(qt_payload), chunk_size):
                _execute_with_retry(
                    supabase.table("quarterly_financials").upsert(
                        qt_payload[i:i+chunk_size],
                        on_conflict="symbol, quarter_date, source_table, metric_name"
                    )
                )
            for q in unsynced_qt:
                q.is_synced = True

        # Commit the is_synced flag updates
        session.commit()
        logger.success(f"[{symbol}] Synced to Supabase successfully.")
        return True

    except Exception as e:
        session.rollback()
        logger.error(f"[{symbol}] Failed to sync to Supabase: {e}")
        return False


# ---------------------------------------------------------------------------
# Bulk pending sync (catches up all unsynced records across all companies)
# ---------------------------------------------------------------------------

def sync_all_pending(max_attempts: int = 3, retry_interval: int = 60) -> bool:
    """
    Finds all records with is_synced=False and pushes them to Supabase.
    If Supabase is unreachable, retries up to max_attempts times with
    retry_interval seconds between attempts.

    Returns True if all pending records were synced, False otherwise.
    """
    if not supabase:
        logger.warning("Supabase credentials not configured. Skipping pending sync.")
        return False

    from data.db import get_db

    for attempt in range(1, max_attempts + 1):
        logger.info(f"Pending sync attempt {attempt}/{max_attempts}...")

        if not _is_supabase_reachable():
            if attempt < max_attempts:
                logger.warning(f"Supabase unreachable. Retrying in {retry_interval}s...")
                time.sleep(retry_interval)
                continue
            else:
                logger.error("Supabase unreachable after all attempts. Pending records remain unsynced.")
                return False

        # Supabase is reachable — sync all pending
        with get_db() as session:
            # Count pending
            pending_ess = session.query(CompanyEssentials).filter_by(is_synced=False).count()
            pending_yr = session.query(YearlyFinancial).filter_by(is_synced=False).count()
            pending_qt = session.query(QuarterlyFinancial).filter_by(is_synced=False).count()
            total_pending = pending_ess + pending_yr + pending_qt

            if total_pending == 0:
                logger.info("No pending records to sync. Everything is up to date!")
                return True

            logger.info(f"Found {total_pending} pending records "
                        f"(Essentials: {pending_ess}, Yearly: {pending_yr}, Quarterly: {pending_qt})")

            # Get all companies that have unsynced data
            symbols = set()
            if pending_ess > 0:
                symbols.update(
                    sym for (sym,) in session.query(CompanyEssentials.symbol)
                    .filter_by(is_synced=False).distinct().all()
                )
            if pending_yr > 0:
                symbols.update(
                    sym for (sym,) in session.query(YearlyFinancial.symbol)
                    .filter_by(is_synced=False).distinct().all()
                )
            if pending_qt > 0:
                symbols.update(
                    sym for (sym,) in session.query(QuarterlyFinancial.symbol)
                    .filter_by(is_synced=False).distinct().all()
                )

            # Resolve symbols
            companies = session.query(Company).filter(Company.symbol.in_(symbols)).all()
            logger.info(f"Syncing {len(companies)} companies with pending data...")

            success = 0
            failed = 0
            for c in companies:
                if sync_company_to_supabase(session, c.symbol):
                    success += 1
                else:
                    failed += 1

            logger.info(f"Pending sync complete: {success} synced, {failed} failed.")
            return failed == 0

    return False
