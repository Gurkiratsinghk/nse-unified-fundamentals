# NSE Unified Fundamentals

A CLI and API tool for accessing, scraping, archiving, and analyzing fundamental financial data for all NSE-listed (National Stock Exchange of India) stocks.

> **Disclaimer:** Currently this project only supports past five years of data.

## Key Features

- **Open-Source Bridge:** Connects you to public financial metrics without the hassle of manual data entry.
- **Dual Storage & High Availability:** Runs primarily against a remote **Supabase (PostgreSQL)** database for high-availability reads, but seamlessly falls back to a **local SQLite** database for offline operations.
- **Smart Storage Limits:** To respect GitHub constraints, the active local SQLite database (`.db`) is tracked via Git LFS, while historical backups are compressed into highly efficient **Parquet** files.
- **Universal Financial Tracking:** Uses an Entity-Attribute-Value (EAV) model to handle diverse accounting structures (e.g., Banks vs. IT) without requiring constant schema migrations. Captures both annual reports and quarterly results.
- **Interactive CLI Application:** Easily search companies, trigger full or partial scrapes (e.g., just the NSE100), and view data without writing code.

---

## Accessing Data via API

If you don't want to run the full scraper pipeline and just want to access the data I have already scraped and hosted, you can connect to my Supabase database directly from your own code. (*Note: The data is updated quarterly*)

### 1. Credentials
To read the data, you will need the following (contact me or check the project settings if you are a collaborator):

```python
# Supabase Configuration
SUPABASE_URL=https://hgakynqiwrzlrtdiepwz.supabase.co
SUPABASE_KEY=sb_publishable_GTnOe8ef-eh4eD8DqFk3pA_-NEd34gl 
```

### 2. Integration Example (Python)
You can pull live data into your own analysis scripts using the `supabase-py` library:

```python
from supabase import create_client

# Initialize client
supabase = create_client("YOUR_PROJECT_URL", "YOUR_ANON_KEY")

# Query company essentials
res = supabase.table("company_essentials").select("*").eq("symbol", "RELIANCE").execute()
print(res.data)
```

---

## Setup Instructions

### 1. Prerequisites
- Python 3.10+
- A [Supabase](https://supabase.com/) account (Free tier) if you intend to host your own version.

### 2. Deployment
If you want to set up your own ingestion pipeline:
1. **Initialize DB:** Run `nse_project/data/supabase_migration.sql` in your Supabase SQL Editor.
2. **Security:** Enable RLS and set up `SELECT` policies as described in `docs/supabase_security_guide.md`.
3. **Environment:** Create a `.env` file with your `SUPABASE_URL` and `SUPABASE_KEY` (use the `service_role` key for the scraper to work).

### 3. Installation
```bash
pip install -r nse_project/requirements.txt
```

---

## Usage

The `main.py` script serves as the unified entry point for all operations.

### Interactive CLI
The easiest way to use the tool is through the interactive terminal dashboard:
```bash
python nse_project/main.py cli
```

### Scraping & Ingestion
```bash
# Full update of all ~2000+ NSE listed equities
python nse_project/main.py full

# Update only the NSE100 index constituents
python nse_project/main.py nse100

# Scrape or refresh a specific company
python nse_project/main.py scrape RELIANCE
```

### Analysis & Utilities
```bash
# Compare key metrics for two companies
python nse_project/main.py compare RELIANCE TCS

# Check database sync status and storage stats
python nse_project/main.py status

# Export financial data to CSV (pivoted format)
python nse_project/main.py export CSV --symbol RELIANCE
```

---

## Documentation

For a deeper dive into how this system works, to avoid the "black box" problem, please review the documentation:
- **`docs/architecture.md`** - How the pipelines, scrapers, and storage interact.
- **`docs/database.md`** - Understanding the EAV schema.
- **`docs/supabase_security_guide.md`** - Securing your public deployments.
- **`docs/api_query_guide.md`** - How to query the database.   

## Future Plans (Fundamental Analysis)

With this structured database in place, upcoming features focus on **Fundamental Analysis (FA)**:
- Automated calculations for **Discounted Cash Flow (DCF)**.
- Historical trend analysis using Financial Ratios (PE, PB, ROCE, ROE, Debt/Equity).
- Building quantitative portfolios based on scraped fundamental indicators.

## Contributing

Contributions are welcome! Feel free to reach me out on:

LinkedIn: https://www.linkedin.com/in/gurkiratsinghkohli/

Instagram: gurkirat_skohl (if unable to connect on LinkedIn)