# NSE Unified Fundamentals Database Schema

This application utilizes a dual-storage strategy: a local SQLite database for offline operations and backups, and a remote Supabase PostgreSQL database for high-availability reads.

## Schema Overview

The schema is built around an Entity-Attribute-Value (EAV) design for financial metrics. This ensures that whenever our data sources expose a new financial metric or ratio, the scraper can ingest it without requiring a database migration.

### 1. `companies` (Master Registry)
Tracks every NSE symbol ever encountered.
- **Columns:** `symbol` (PK), `company_name`, `sector`, `industry`, `isin`.
- **Purpose:** Central entity for foreign keys.

### 2. `company_essentials`
Stores the latest snapshot of a company's fundamental ratios (e.g., PE Ratio, ROCE, Debt to Equity).
- **Columns:** `symbol` (FK), `metric_name`, `value_num`, `value_text`, `scraped_at`.
- **Constraint:** Unique across `(symbol, metric_name)`. Always upserted.

### 3. `yearly_financials`
Stores longitudinal financial data extracted from Profit & Loss, Balance Sheet, and Cash Flow tables.
- **Columns:** `symbol`, `fiscal_year` (e.g., 2024), `source_table`, `metric_name`, `value_num`, `value_text`.
- **Constraint:** Unique across `(symbol, fiscal_year, source_table, metric_name)`.

### 4. `quarterly_financials`
Stores recent quarterly results.
- **Columns:** `symbol`, `quarter_date` (e.g., "Dec 2025"), `source_table`, `metric_name`, `value_num`, `value_text`.
- **Constraint:** Unique across `(symbol, quarter_date, source_table, metric_name)`.

## SQL Migration
The remote Supabase schema exactly mirrors the local SQLite ORM models. See `nse_project/data/supabase_migration.sql` to initialize the remote database. By design, the scraper uses `UPSERT` on the unique constraints to prevent duplicates during interrupted runs.
