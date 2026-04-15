# NSE Unified Fundamentals Architecture

This repository holds a CLI application aimed at creating a robust bridge to publicly available financial data for NSE (National Stock Exchange) listed companies. It provides an automated ingestion pipeline that writes to a database, facilitating fundamental analysis.

## Core Components

### 1. The CLI Client
- Located in `nse_project/client/cli.py`
- Built using `argparse` and `rich`, providing interactive progress reporting.
- Allows running full scrapes, specific ticker scrapes, and database initializations.

### 2. The Ingestion Pipeline
- Located in `nse_project/pipeline/ingest.py`
- Orchestrates the sequence of events:
  1. Initialize local and remote databases.
  2. Scrape the master lists of tickers (NSE Top 100 or All bounds).
  3. Iterate through tickers to scrape their financial fundamentals.
  4. Perform data validation, cleansing, and persistence.

### 3. The Scrapers
- Located in `nse_project/scrapers/`
- `nse_list.py`: Queries NSE endpoints to retrieve all listed active equities.
- `nse100_list.py`: Identifies the top 100 companies by market capitalization.
- `financials.py`: Iterats over symbols to retrieve raw financial ratios and business metrics (e.g. PE Ratio, ROCE, ROE, Debt) from open public data sources.

### 4. Storage Layer
- Built using an **Offline-First / Dual-Storage strategy**.
- **Local SQLite:** Serves as a fallback offline store (`nse_fundamentals.db`). For archival storage, backups are compressed to Parquet format.
- **Supabase (PostgreSQL):** Serves as the primary source of truth for downstream APIs and remote queries. The CLI leverages the `supabase-py` SDK to push upserts.

## The "Black Box" Prevention Matrix

To avoid making this tool a black box for engineers:
- **No obscure magic:** All HTTP requests use standard `requests` behavior with explicit headers and retries.
- **Documented Schemas:** Open `docs/database.md` to see exactly what shapes the tables take.
- **Explicit Upserts:** We rely on `upsert` semantics matching by `symbol`, meaning idempotent runs are safe. If a scrape stops halfway, re-running it simply overwrites or skips identical records without duplication.
- **Detailed Logging:** The logging system outputs all failures. Records that fail to scrape remain marked `is_synced = 0` in the database, making it trivial to run SQL queries identifying problematic tickers.
