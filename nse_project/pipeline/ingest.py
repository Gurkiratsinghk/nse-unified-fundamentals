"""
Ingest pipeline — orchestrates end-to-end scraping runs.

Supports two modes:
  1. NSE100 mode  (run_full_ingest)     — original NSE100 index constituents only
  2. Full NSE mode (run_full_nse_ingest) — ALL NSE-listed equities from EQUITY_L.csv

Pipeline order:
  1. rotate_backups()             — save up to 3 rolling DB backups
  2. init_db()                    — ensure all tables exist
  3. sync_all_pending()           — catch up any previously unsynced records
  4. fetch company list           — sync master list
  5. run_scraper_for_symbols()    — scrape + persist Data Source data
  6. sync_all_pending()           — final push of anything new

Both modes are safe to run repeatedly — existing rows are preserved.
"""

import sys
import json
import signal
from pathlib import Path
from datetime import datetime
from loguru import logger

from data.db import init_db, get_db, rotate_backups
from data.models import Company
from data.supabase_sync import sync_all_pending
from scrapers.nse100_list import fetch_and_update_nse100, get_all_scrape_symbols, get_active_symbols
from scrapers.nse_list import fetch_nse_equities
from scrapers.financials import run_scraper_for_symbols

_STATE_FILE = Path(__file__).parent.parent / "data" / "scrape_state.json"


def _signal_handler(sig, frame):
    """Handle Ctrl+C gracefully."""
    logger.warning("\nInterrupt received (Ctrl+C). Cleaning up and exiting...")
    # Any active session should be rolled back/closed by the context managers
    sys.exit(0)

# Register signal handler
signal.signal(signal.SIGINT, _signal_handler)


def setup_logging():
    """Configure loguru to write to both stdout and a file."""
    # Ensure logs directory exists
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d")
    log_file = log_dir / f"scrape_{timestamp}.log"
    
    # We don't remove existing handlers to keep stdout clean (already configured by loguru)
    logger.add(log_file, rotation="10 MB", level="INFO", format="{time} {level} {message}")
    logger.info(f"Logging to {log_file}")


def _save_resume_state(mode: str, last_symbol: str):
    """Save the last successfully processed symbol for a given mode."""
    state = {}
    if _STATE_FILE.exists():
        try:
            with open(_STATE_FILE, "r") as f:
                state = json.load(f)
        except Exception:
            pass
    
    state[mode] = {
        "last_symbol": last_symbol,
        "timestamp": datetime.now().isoformat()
    }
    
    with open(_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _load_resume_state(mode: str) -> str | None:
    """Load the last symbol for a given mode if it's recent (same day)."""
    if not _STATE_FILE.exists():
        return None
        
    try:
        with open(_STATE_FILE, "r") as f:
            state = json.load(f)
            mode_state = state.get(mode)
            if mode_state:
                # Check if it was from today
                ts = datetime.fromisoformat(mode_state["timestamp"])
                if ts.date() == datetime.now().date():
                    return mode_state["last_symbol"]
    except Exception:
        pass
    return None


def _clear_resume_state(mode: str):
    """Clear the resume state for a mode after successful full completion."""
    if not _STATE_FILE.exists():
        return
    try:
        with open(_STATE_FILE, "r") as f:
            state = json.load(f)
        if mode in state:
            del state[mode]
            with open(_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
    except Exception:
        pass


def _pre_ingest(mode="full"):
    """Common pre-ingest steps: backup DB, init tables, catch up pending syncs."""
    setup_logging()
    logger.info(f"Pre-ingest: Rotating database backups (mode: {mode})...")
    rotate_backups(mode=mode)

    logger.info("Pre-ingest: Initialising database...")
    init_db()

    logger.info("Pre-ingest: Catching up any previously unsynced records...")
    sync_all_pending(max_attempts=1, retry_interval=5)


def _post_ingest():
    """Common post-ingest steps: final sync push."""
    logger.info("Post-ingest: Pushing newly scraped data to Supabase...")
    sync_all_pending(max_attempts=3, retry_interval=30)


def run_single_scrape(symbol: str) -> None:
    """
    Scrape a single company by symbol.
    """
    logger.info("=" * 60)
    logger.info(f"SINGLE SYMBOL SCRAPE — {symbol} STARTED")
    logger.info("=" * 60)

    # For single scrape, we don't necessarily want to rotate the full DB
    # backup every time, but init_db is needed.
    init_db()
    
    # We might want to ensure the company exists in our master list first
    with get_db() as session:
        company = session.query(Company).filter_by(symbol=symbol).first()
        if not company:
            logger.info(f"Symbol {symbol} not found in master list. Adding stub...")
            company = Company(symbol=symbol, company_name=symbol)
            session.add(company)
            session.commit()

    success, failure = run_scraper_for_symbols([symbol])

    _post_ingest()

    logger.info("=" * 60)
    logger.info(f"SINGLE SYMBOL SCRAPE — {symbol} COMPLETE")
    logger.info(f"  OK : {success}  |  Failed : {failure}")
    logger.info("=" * 60)


def run_full_ingest() -> None:
    """
    Execute the NSE100 ingest pipeline (original behaviour).
    """
    mode = "nse100"
    logger.info("=" * 60)
    logger.info("NSE100 FUNDAMENTALS — FULL INGEST STARTED")
    logger.info("=" * 60)

    _pre_ingest(mode=mode)

    logger.info("Step 1/3: Updating NSE100 constituent list...")
    fetch_and_update_nse100()

    logger.info("Step 2/3: Resolving scrape targets from DB...")
    all_symbols = get_all_scrape_symbols()
    
    # Resume logic
    last_symbol = _load_resume_state(mode)
    if last_symbol and last_symbol in all_symbols:
        idx = all_symbols.index(last_symbol)
        all_symbols = all_symbols[idx + 1:]
        logger.info(f"Resuming {mode} from {last_symbol} (skipping {idx + 1} completed symbols)")

    if not all_symbols:
        logger.warning("No new symbols to scrape — clearing resume state and finishing.")
        _clear_resume_state(mode)
        return

    logger.info(f"Step 3/3: Scraping Data Source for {len(all_symbols)} companies...")
    success, failure = run_scraper_for_symbols(
        all_symbols, 
        callback=lambda s, ok: _save_resume_state(mode, s) if ok else None
    )

    _clear_resume_state(mode)
    _post_ingest()

    logger.info("=" * 60)
    logger.info("INGEST COMPLETE")
    logger.info(f"  Attempted : {len(all_symbols)}  |  OK : {success}  |  Failed : {failure}")
    logger.info("=" * 60)


def run_full_nse_ingest() -> None:
    """
    Execute the FULL NSE ingest pipeline — all listed equities.
    """
    mode = "full"
    logger.info("=" * 60)
    logger.info("FULL NSE FUNDAMENTALS — INGEST STARTED")
    logger.info("=" * 60)

    _pre_ingest(mode=mode)

    # Step 1 — pull the complete active equities list from NSE
    logger.info("Step 1/3: Updating master companies list from NSE EQUITY_L.csv...")
    fetch_nse_equities()

    # Step 2 — get every symbol in the companies table
    logger.info("Step 2/3: Resolving all scrape targets...")
    with get_db() as session:
        all_symbols = [c.symbol for c in session.query(Company).order_by(Company.symbol).all()]

    # Resume logic
    last_symbol = _load_resume_state(mode)
    if last_symbol and last_symbol in all_symbols:
        idx = all_symbols.index(last_symbol)
        all_symbols = all_symbols[idx + 1:]
        logger.info(f"Resuming {mode} from {last_symbol} (skipping {idx + 1} completed symbols)")

    logger.info(f"  Total companies to scrape: {len(all_symbols)}")

    if not all_symbols:
        logger.warning("No new symbols to scrape — clearing resume state and finishing.")
        _clear_resume_state(mode)
        return

    # Step 3 — scrape Data Source
    logger.info(f"Step 3/3: Scraping Data Source for {len(all_symbols)} companies...")
    success, failure = run_scraper_for_symbols(
        all_symbols,
        callback=lambda s, ok: _save_resume_state(mode, s) if ok else None
    )

    _clear_resume_state(mode)
    _post_ingest()

    logger.info("=" * 60)
    logger.info("FULL NSE INGEST COMPLETE")
    logger.info(f"  Attempted : {len(all_symbols)}  |  OK : {success}  |  Failed : {failure}")
    logger.info("=" * 60)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "nse100"
    if mode == "full":
        run_full_nse_ingest()
    else:
        run_full_ingest()