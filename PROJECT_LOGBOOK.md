# PROJECT LOGBOOK — NSE Unified Fundamentals

> **Last Updated:** 2026-04-27  
> **Author:** Gurkirat Singh Kohli  
> **Repository:** `Gurkiratsinghk/nse-unified-fundamentals`  
> **Purpose:** Context backup for cross-instance continuity. Written to provide a new AI assistant or developer with *complete* knowledge of the system's current state without needing to re-derive anything from source code alone.

---

## Table of Contents

1. [Logic Flow & Concepts](#1-logic-flow--concepts)
2. [Changelog & Architecture](#2-changelog--architecture)
3. [Technical Debt & Stability](#3-technical-debt--stability)
4. [Operational Impact](#4-operational-impact)
5. [Pending Tasks](#5-pending-tasks)

---

## 1. Logic Flow & Concepts

### 1.1 Project Purpose

A Python CLI and API tool that scrapes, stores, and serves fundamental financial data for **all NSE-listed (National Stock Exchange of India) equities**. It acts as an open-source bridge between publicly available financial data (from `ticker.finology.in`) and a clean, queryable database. Targets retail investors, quantitative analysts, and financial developers who cannot afford enterprise-grade data APIs.

### 1.2 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Entry Point                                  │
│               nse_project/main.py                                   │
│    (cli | full | nse100 | scrape <SYM> | status | compare | export) │
└───────────┬─────────────────────────────────────────────────────────┘
            │
            ▼
┌───────────────────────────────────────┐
│         Ingestion Pipeline            │
│     nse_project/pipeline/ingest.py    │
│                                       │
│  1. rotate_backups() → Parquet        │
│  2. init_db()                         │
│  3. sync_all_pending() → Supabase     │
│  4. Fetch company lists               │
│  5. run_scraper_for_symbols()         │
│  6. sync_all_pending() → final push   │
└───────────┬───────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────┐
│                     Scrapers Layer                            │
│                                                              │
│  nse_list.py          → EQUITY_L.csv from NSE archives       │
│  nse100_list.py       → NIFTY 100 API + constituent audit    │
│  financials.py        → Per-company financials from Finology │
│    ├─ Cloudflare bypass (SeleniumBase UC → curl_cffi)        │
│    ├─ HTML parsing (BeautifulSoup + lxml)                    │
│    ├─ Essentials parser (#companyessentials div)             │
│    ├─ Financial table parser (P&L, BS, CF, Shareholding)     │
│    └─ Quarterly results parser                               │
└───────────┬──────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────┐
│                     Storage Layer                             │
│                                                              │
│  Local SQLite (nse_all_stocks.db)                            │
│    ├─ ORM via SQLAlchemy (data/models.py)                    │
│    ├─ Session management (data/db.py)                        │
│    └─ Rolling Parquet backups (Snappy compression)           │
│                                                              │
│  Remote Supabase (PostgreSQL)                                │
│    ├─ Primary read source for consumers                      │
│    ├─ Delta-sync via is_synced flag (data/supabase_sync.py)  │
│    └─ Schema: supabase_migration.sql                         │
│                                                              │
│  Git LFS                                                     │
│    ├─ *.db files tracked via LFS                             │
│    └─ *.parquet files tracked via LFS                        │
└──────────────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────┐
│                  Client / Consumer Layer                      │
│                                                              │
│  CLI Viewer (client/cli.py)                                  │
│    ├─ Rich-powered interactive terminal dashboard            │
│    ├─ 8-option menu (search, list, essentials, annual,       │
│    │   quarterly, compare, export, status)                   │
│    └─ Supabase-first with automatic local SQLite fallback    │
│                                                              │
│  Programmatic API (client/api.py)                            │
│    └─ Supabase SDK functions for external integration        │
└──────────────────────────────────────────────────────────────┘
```

### 1.3 Data Model — Entity-Attribute-Value (EAV)

The core schema decision was adopting an **EAV (Entity-Attribute-Value)** model for financial metrics. This was chosen over a traditional wide-table approach because:

- **Different company types** (Banks, IT, Manufacturing) expose different accounting line items. A wide table would require constant schema migrations.
- **New metrics** appear on the source website without warning. EAV lets the scraper ingest anything new automatically.
- **Trade-off accepted:** Pivoting EAV data for display is more expensive at query time, but scraping frequency is low (quarterly) so this is acceptable.

**Tables:**

| Table | Key Constraint | Write Strategy | Purpose |
|---|---|---|---|
| `companies` | PK: `symbol` | Upsert | Master registry of every NSE ticker |
| `nse100_constituents` | FK → companies | Insert + soft-delete (`removed_date`) | Audit trail of NIFTY 100 membership |
| `company_essentials` | Unique: `(symbol, metric_name)` | Upsert (latest snapshot) | KPIs: Market Cap, PE, ROE, ROCE, etc. |
| `yearly_financials` | Unique: `(symbol, fiscal_year, source_table, metric_name)` | Insert-only (historical) | P&L, Balance Sheet, Cash Flow, Shareholding |
| `quarterly_financials` | Unique: `(symbol, quarter_date, source_table, metric_name)` | Insert-only (historical) | Quarterly results |
| `rankings` | FK → companies | Stub (unused) | Future: automated ranking engine |
| `portfolio` | FK → companies | Stub (unused) | Future: quantitative portfolios |

### 1.4 Dual-Storage & Sync Strategy

The project follows an **Offline-First / Dual-Storage** pattern:

1. **Local SQLite** is the write-through store during scraping. All data hits SQLite first.
2. **Supabase (PostgreSQL)** is the primary read source for consumers/APIs.
3. A `is_synced` boolean flag on each data row tracks whether it has been pushed to Supabase.
4. `sync_company_to_supabase()` is called after each company scrape — delta-sync (only unsynced records).
5. `sync_all_pending()` is called at pipeline start (catch-up) and end (final push) — bulk sync with retry.
6. If Supabase is unreachable, scraping continues locally. Data is pushed later via `sync_all_to_supabase.py` or the next pipeline run.

**Why not Supabase-only?** GitHub Actions runners have flaky network connections. Local SQLite ensures no data loss if network drops mid-scrape. The `is_synced` flag guarantees eventual consistency.

### 1.5 Cloudflare Bypass Strategy (Critical Concept)

`ticker.finology.in` is protected by **Cloudflare Turnstile** (JS challenge + bot detection). GitHub Actions runners use datacenter IPs, which are immediately flagged. The bypass uses a **hybrid browser → HTTP handoff** approach:

```
Phase 1: SeleniumBase UC Mode (headed, in Xvfb virtual display)
  ├─ Opens ticker.finology.in/company/TCS
  ├─ Solves Cloudflare Turnstile via uc_gui_click_captcha()
  ├─ Extracts cf_clearance cookie
  ├─ Extracts full browser fingerprint (User-Agent, sec-ch-ua, Accept-Language)
  └─ Shuts down browser

Phase 2: curl_cffi (impersonate="chrome")
  ├─ Receives cf_clearance cookie + browser headers
  ├─ TLS fingerprint (JA3) matches real Chrome
  ├─ Performs high-speed HTTP scraping for all symbols
  └─ Refreshes cookie every 60 requests (_COOKIE_REFRESH_INTERVAL)
```

**Why this approach over alternatives:**

| Alternative | Why Rejected |
|---|---|
| `cloudscraper` | TLS fingerprint mismatch detected by Cloudflare (tested, failed) |
| `tls_client` | API timeout parameter issues; less mature than curl_cffi |
| Selenium for all requests | Too slow (2000+ companies × headless Chrome overhead) |
| Static cookie injection | `cf_clearance` has a short TTL; must be solved live |
| `requests` with headers | No TLS fingerprint impersonation; blocked immediately |

### 1.6 Value Normalization Rules

A significant amount of engineering went into parsing raw financial values correctly:

| Source Format | Stored As | Rule |
|---|---|---|
| `"5507.82 Cr."` | `55078200000.0` | Multiply by 10,000,000 (1 Cr = 10^7) |
| `"28.80%"` | `0.288` | Divide by 100 |
| `"Rs 1,000,000"` | `1000000.0` | Strip Rs, commas, ₹ |
| `"-"` or `"--"` | `NULL` | Not zero — explicit null |
| `"Some Date"` | `value_text="Some Date"` | Non-numeric → stored as text |
| Percentage metric with `default_to_crores=True` | NOT scaled to crores | `pct` in metric key prevents Cr scaling |

**Critical edge case handled:** The essentials parser skips labels that look like numbers (`_is_numeric_like()`). Finology sometimes puts numeric data in the `<small>` tag where a label should be.

### 1.7 Resume & Idempotency

- **Resume state:** `scrape_state.json` saves the last successfully processed symbol per mode (`full` / `nse100`). If a scrape is interrupted (Ctrl+C, crash, timeout), the next run resumes from where it left off.
- **Same-day only:** Resume state is only valid for the same calendar day. Next-day runs start fresh.
- **Idempotency:** Essentials are upserted (latest wins). Yearly/quarterly data is insert-only (unique constraints prevent duplicates). Re-running is always safe.

### 1.8 Special Symbol Handling

Symbols containing `&` (e.g., `M&M`, `L&TFH`, `J&KBANK`) cause URL encoding issues. A multi-layer resolution strategy is used:

1. **Hardcoded `_SPECIAL_CASES` dict** — maps problematic symbols to known SCRIP-XXXXX URLs.
2. **URL encoding** — `urllib.parse.quote(symbol, safe="")` encodes `&` as `%26`.
3. **Redirect following** — Finology sometimes redirects to canonical SCRIP URLs.
4. **Search fallback** — Queries Finology's search API (`GetSearchData.ashx`) to resolve FINCODE, then constructs the SCRIP URL.

---

## 2. Changelog & Architecture

### 2.1 Commit Timeline & Reasoning

| Date | Commit | Change | Reasoning |
|---|---|---|---|
| 2026-04-15 | `b372f9d` | **Initial Commit** | Full project scaffold: ORM models, CLI, pipeline, NSE100 scraper, Supabase sync, docs. Everything built from prior prototype (`nse100_fa/`). |
| 2026-04-16 | `86e88a2` | Updated README + API guide | Documented public Supabase credentials and query patterns for external consumers. |
| 2026-04-16 | `2775da9` | `cloudscraper` integration | First attempt at bypassing Cloudflare. *Failed:* TLS fingerprint mismatch. |
| 2026-04-16 | `95df753` | CI workflow update | Ensured tests run on push to `master`. |
| 2026-04-16 | `d7085e3` | **Replace cloudscraper → curl_cffi** | `curl_cffi` impersonates Chrome's TLS JA3 fingerprint. This was the breakthrough — initial 403s resolved. |
| 2026-04-16 | `62d8f57` | Enable Git LFS + pyarrow | `.db` and `.parquet` files tracked via LFS to prevent repo bloat. Added pyarrow for Parquet backups. Stopped overriding User-Agent/Accept-Language to avoid fingerprint mismatch. |
| 2026-04-16 | `f3744d5` | Created test bypass workflow | Cross-platform (Ubuntu/Windows/macOS) matrix to validate curl_cffi bypass. |
| 2026-04-16 | `a21e0bf` → `d625044` | Dependency fixes | Fixed missing test deps and tls_client timeout parameter issues. |
| 2026-04-16 | `ff652a1` | Test SeleniumBase UC mode | Proved SeleniumBase can solve Cloudflare Turnstile in CI. |
| 2026-04-17 | `f9293b5` | **Test cookie handoff** | Validated the hybrid approach: SeleniumBase solves challenge → extracts `cf_clearance` → curl_cffi uses it. (`test_bypass.py`)|
| 2026-04-17 | `f8fa825` | OS matrix + CF_CLEARANCE | Tested cookie injection across operating systems. |
| 2026-04-17 | `4a9d245` | **Integrated hybrid bypass into main scraper** | `_init_cloudflare_bypass()` added to `financials.py`. SeleniumBase runs in Xvfb, extracts cookie + headers, injects into curl_cffi session. |
| 2026-04-17 | `1d79c75` | Removed pyautogui dep | SeleniumBase unpacking errors on headless Linux. PyAutoGUI requires a display; incompatible with pure headless. |
| 2026-04-17 | `76ab0c6` | Delegated Xvfb to SB | Tried using SeleniumBase's built-in `xvfb=True`. *Partially worked.* |
| 2026-04-17 | `3bdaa54` | **Restored manual Xvfb** | Final working solution: manual `pyvirtualdisplay.Display` started BEFORE SeleniumBase import. This ensures `DISPLAY` env var is set for Xlib. Re-added pyautogui + python3-Xlib as deps. |
| 2026-04-17 | `ea469d8` | Fix missing `os` import | NameError in `financials.py` — `os.environ` used but `os` not imported. |
| 2026-04-17 | `b69ad9c` | **Jitter + cookie refresh + full header extraction** | Anti-detection hardening: randomized sleep with rare "human reading" pauses (10% chance of 5–12s extra delay), cookie refresh every 60 requests, extraction of `sec-ch-ua` / `sec-ch-ua-platform` headers from browser. |

### 2.2 Files Inventory with Stability Assessment

| File | Lines | Role | Last Major Change |
|---|---|---|---|
| `nse_project/main.py` | 54 | CLI dispatcher / entry point | Initial commit |
| `nse_project/client/cli.py` | 498 | Rich interactive terminal UI | Refactoring conversation |
| `nse_project/client/api.py` | 76 | Programmatic Supabase query functions | Initial commit |
| `nse_project/pipeline/ingest.py` | 262 | Orchestration: backup → init → scrape → sync | Refactoring conversation |
| `nse_project/scrapers/financials.py` | 969 | Core scraper + Cloudflare bypass | Most recent (b69ad9c) |
| `nse_project/scrapers/nse100_list.py` | 313 | NSE100 API scraper + constituent tracking | Initial + refactoring |
| `nse_project/scrapers/nse_list.py` | 76 | Full NSE equity list from CSV | Initial commit |
| `nse_project/data/models.py` | 204 | SQLAlchemy ORM table definitions | Refactoring (added `is_synced`) |
| `nse_project/data/db.py` | 170 | Engine, sessions, backup rotation, migrations | Refactoring (Parquet, mode-aware backups) |
| `nse_project/data/supabase_sync.py` | 256 | Delta-sync engine with retry logic | Refactoring conversation |
| `nse_project/data/supabase_migration.sql` | 71 | Supabase schema DDL | Initial commit |
| `nse_project/scripts/keep_alive_supabase.py` | 25 | Supabase ping to prevent suspension | CI/CD conversation |
| `nse_project/sync_all_to_supabase.py` | 29 | Manual bulk sync utility | Refactoring conversation |
| `nse_project/requirements.txt` | 19 | Python dependencies | Cloudflare bypass conversation |
| `tests/test_parsers.py` | 55 | Unit tests for parsing functions | Initial + refactoring |
| `test_bypass.py` | 86 | E2E test: SeleniumBase → curl_cffi handoff | Cloudflare bypass conversation |
| `.github/workflows/ci.yml` | 32 | Run pytest on push/PR | CI/CD conversation |
| `.github/workflows/quarterly_scrape.yml` | 55 | Cron: quarterly full NSE scrape + DB commit | CI/CD conversation |
| `.github/workflows/keep_alive.yml` | 33 | Cron: every 3 days, ping Supabase | CI/CD conversation |
| `.github/workflows/test_bypass.yml` | 32 | Matrix test: bypass on Ubuntu/Windows/macOS | Cloudflare bypass conversation |
| `docs/architecture.md` | 38 | Architecture overview | Refactoring conversation |
| `docs/database.md` | 31 | EAV schema documentation | Refactoring conversation |
| `docs/api_query_guide.md` | 139 | Supabase query cookbook | Initial commit |
| `docs/supabase_security_guide.md` | 50 | RLS + policy setup guide | Refactoring conversation |
| `docs/linkedin_post_draft.md` | 26 | Launch announcement draft | Refactoring conversation |
| `.gitignore` | 36 | Excludes builds, logs, scratch, env | Refactoring conversation |
| `.gitattributes` | 5 | Git LFS tracking for .db/.parquet | Cloudflare bypass conversation |
| `nse_cli.spec` | 39 | PyInstaller spec for compiled CLI | Refactoring conversation |
| `NSE data/` | - | Local data directory (gitignored) | Legacy |

### 2.3 Key Design Decisions & Alternatives Considered

| Decision | Chosen | Alternative Considered | Rationale |
|---|---|---|---|
| Schema design | EAV | Wide tables per statement type | Avoid schema migrations when new financial metrics appear |
| Primary data store | SQLite + Supabase dual | Supabase only | Resilience: scraping must never fail due to network issues |
| Local backup format | Parquet (Snappy) | Additional .db copies | Compression ratio ~10x; Git LFS handles active .db |
| HTTP library | curl_cffi | cloudscraper, tls_client, requests | Only lib that reliably impersonates Chrome JA3 TLS fingerprint |
| Cloudflare solve | SeleniumBase UC (headed in Xvfb) | Playwright, raw Selenium | UC mode specifically designed for anti-bot; `uc_gui_click_captcha()` |
| CLI framework | Rich + argparse | Click, Typer | Rich provides beautiful terminal output; argparse is stdlib |
| Logging | Loguru | stdlib logging | Cleaner API, colored output, rotation built-in |
| Sync flag | `is_synced` boolean per row | Timestamp-based diff | Simple, deterministic, survives partial failures |
| Backup rotation | Mode-aware (`backup_full_bak1`, `backup_nse100_bak1`) | Single rotation regardless of mode | Prevents NSE100 backup from overwriting a full backup |

---

## 3. Technical Debt & Stability

### 3.1 Stability Matrix

| Component | Stability | Assessment |
|---|---|---|
| **ORM Models** (`models.py`) | 🟢 Production-ready | Clean, well-constrained, migration-safe. EAV unique constraints are correct. |
| **Database Layer** (`db.py`) | 🟢 Production-ready | Corrupt DB detection, auto-migration (`_migrate_add_is_synced`), Parquet backups all work. |
| **Supabase Sync** (`supabase_sync.py`) | 🟢 Stable | Delta-sync, chunked upserts (500 rows), exponential backoff retry, connectivity check. |
| **NSE100 List Scraper** (`nse100_list.py`) | 🟢 Stable | Cookie handshake + content-type guard. Constituent audit trail is clean. |
| **NSE Full List Scraper** (`nse_list.py`) | 🟢 Stable | Simple CSV download; no anti-bot concerns. |
| **CLI Client** (`cli.py`) | 🟢 Stable | Supabase-first with automatic fallback; well-formatted Rich output. |
| **Ingestion Pipeline** (`ingest.py`) | 🟢 Stable | Resume state, signal handler (Ctrl+C), pre/post hooks, mode-aware backups. |
| **Unit Tests** (`test_parsers.py`) | 🟡 Adequate | Covers `_to_snake`, `_clean_numeric_string`, `_parse_value`. Missing: table parser tests, integration tests. |
| **Financials Scraper** (`financials.py`) | 🟡 Functional but fragile | The largest file (969 lines). Parsing logic is solid, but the Cloudflare bypass is inherently brittle — tied to Cloudflare's challenge implementation. |
| **Cloudflare Bypass** (in `financials.py`) | 🔴 Quick-and-dirty | Works today, but: (a) depends on Cloudflare not changing Turnstile, (b) `uc_gui_click_captcha()` relies on pixel coordinates in Xvfb, (c) `cf_clearance` TTL is unknown — 60-request refresh is empirical, (d) pyautogui + Xlib are fragile on headless Linux. |
| **Test Bypass Script** (`test_bypass.py`) | 🟡 Dev tool only | Useful for validation but not a proper test. Manual invocation only. |
| **Keep-Alive Script** (`keep_alive_supabase.py`) | 🟢 Production-ready | Simple, does one thing well. Prevents Supabase free-tier suspension. |
| **CI/CD Workflows** | 🟡 Functional | `ci.yml` runs tests. `quarterly_scrape.yml` needs verification with actual Cloudflare bypass in Actions. `keep_alive.yml` works. `test_bypass.yml` is a diagnostic tool. |

### 3.2 Known Technical Debt

1. **`financials.py` is 969 lines** — the Cloudflare bypass, HTML parsing, DB writes, and orchestration loop are all in one file. Should be split into:
   - `scrapers/bypass.py` — Cloudflare solution logic
   - `scrapers/parsers.py` — HTML parsing functions
   - `scrapers/financials.py` — orchestration only

2. **Table index positions are hardcoded** — The scraper relies on `_TABLE_IDX_PL = 2`, `_TABLE_IDX_BS = 3`, etc. If Finology changes their HTML structure, parsing breaks silently.

3. **No integration tests** — The unit tests cover parsing functions but there's no end-to-end test that scrapes a real page and validates the full pipeline.

4. **`_SPECIAL_CASES` dict is manual** — The ampersand symbol mapping is hardcoded. If more edge cases arise, this won't scale.

5. **No data validation layer** — Scraped values are trusted directly. No sanity checks (e.g., "market cap should be > 0", "PE ratio shouldn't be 50,000").

6. **SQLite `company_id` FK vs Supabase `symbol` FK mismatch** — Local SQLite uses integer `company_id` as FK, but Supabase uses `symbol` (TEXT) as FK. The sync layer translates, but this is a structural inconsistency.

7. **`pyautogui` + `python3-Xlib` requirements** — These are only needed for the Cloudflare bypass on Linux CI. They add ~50MB of dependencies for a feature that only runs in GitHub Actions.

8. **`nse_cli.spec` is stale** — The PyInstaller spec doesn't include the new dependencies (curl_cffi, seleniumbase, etc.) and has no `datas` entries for the database or configs.

9. **Quarterly scrape workflow untested end-to-end** — `quarterly_scrape.yml` has the right structure but hasn't been validated with the full Cloudflare bypass integrated.

10. **Stub tables (`rankings`, `portfolio`)** — Defined in models but completely unused. Placeholder for future fundamental analysis features.

### 3.3 Security Considerations

| Item | Status | Notes |
|---|---|---|
| `.env` in `.gitignore` | ✅ Configured | Service role key never committed |
| Supabase RLS | ✅ Documented | Guide in `docs/supabase_security_guide.md` |
| Public anon key in README | ⚠️ Intentional | Read-only; RLS restricts writes |
| `service_role` key in GH Secrets | ✅ Configured | Used by `quarterly_scrape.yml` and `keep_alive.yml` |
| No rate limiting on public API reads | ⚠️ Accepted risk | Supabase handles this at infra level |

---

## 4. Operational Impact

### 4.1 Current System Scale

| Metric | Value |
|---|---|
| Total companies in database | ~2,100+ (all NSE EQ series) |
| Active SQLite database size | ~93 MB |
| Database tables | 6 (4 data + 2 stubs) |
| Financial metrics per company | ~15 essentials + ~50 yearly + ~30 quarterly |
| Historical data depth | Last 5 fiscal years |
| Scrape time (full NSE, estimated) | 4–6 hours (with jitter + cookie refresh) |
| Scrape time (NSE100) | ~30–45 minutes |

### 4.2 Impact of Changes Made

#### Anti-Bot Detection (Sessions: April 16–17, 2026)

- **Before:** Scraper used plain `requests` → blocked immediately (403 Forbidden).
- **After:** Hybrid SeleniumBase + curl_cffi bypass → scraping works on both local machines and GitHub Actions.
- **Performance cost:** Initial challenge solve adds ~30s overhead. Cookie refresh every 60 requests adds ~30s per refresh. Jitter adds ~1–3s per request (previously fixed 2s).
- **Reliability gain:** The jitter + cookie refresh reduced mid-run 403 failures from frequent to near-zero.

#### Data Integrity Fixes (Sessions: March 4–21, 2026)

- **Crore scaling:** Values like `"5507.82 Cr."` were initially stored as `5507.82` (raw). Now correctly stored as `55,078,200,000` (absolute Rupees). This is a **breaking change for downstream consumers** — they must divide by 10^7 to get Crores back.
- **Percentage normalization:** `"28.80%"` now stored as `0.288` (decimal), not `28.80`.
- **Metric name corruption fix:** Numeric values were being stored in `metric_name` column due to malformed HTML. Fixed with `_is_numeric_like()` guard.

#### CI/CD Automation (Sessions: April 15–16, 2026)

- **Quarterly scrape:** Runs on 1st of Jan/Apr/Jul/Oct. Scrapes all NSE, pushes DB to repo via git commit.
- **Keep-alive:** Pings Supabase every 3 days to prevent free-tier project suspension (7-day inactivity threshold).
- **Test CI:** Runs `pytest tests/` on every push/PR to `master`.

#### Storage Optimization (Session: March 27, 2026)

- **Git LFS:** `.db` and `.parquet` files tracked via LFS. Prevents repo size from growing quadratically with each scrape.
- **Parquet backups:** Replaced rolling `.db.bak` copies with Parquet (Snappy-compressed). Compression ratio is ~10x.
- **Mode-aware rotation:** `backup_full_bak1` vs `backup_nse100_bak1` prevents cross-mode overwrites.

### 4.3 Dependency Tree (Runtime)

```
Core:        requests, beautifulsoup4, lxml, sqlalchemy, pandas, loguru, rich
Data:        supabase-py, python-dotenv, pyarrow, pyyaml
Anti-bot:    curl_cffi, seleniumbase, pyvirtualdisplay, pyautogui, python3-Xlib
Scheduling:  schedule (unused currently — legacy from earlier prototype)
```

---

## 5. Pending Tasks

### 5.1 Critical (Must-do next)

- [ ] **Validate `quarterly_scrape.yml` end-to-end in GitHub Actions** — The workflow exists but the full Cloudflare bypass + scrape + DB commit flow has not been tested in CI. Trigger a manual `workflow_dispatch` run and monitor.
- [ ] **Verify Supabase data after the next full scrape** — After the Crore scaling fix, confirm that downstream consumers (and the CLI viewer) display correct values.
- [ ] **Re-scrape existing companies** — The Crore scaling and percentage normalization changes mean old data in the DB may be in the wrong units. Consider a one-time full re-scrape to normalize historical data.

### 5.2 Important (Short-term)

- [ ] **Split `financials.py`** into `bypass.py`, `parsers.py`, and a leaner `financials.py` — currently 969 lines, hard to maintain.
- [ ] **Add integration tests** — Test the full scrape-and-persist flow for at least 2 known tickers (TCS, RELIANCE).
- [ ] **Add data validation guards** — Sanity-check scraped values (e.g., market cap > 0, PE ratio within reasonable bounds) before DB insert.
- [ ] **Handle table index changes gracefully** — Instead of hardcoded `_TABLE_IDX_PL = 2`, detect tables by their header content (e.g., look for `"Revenue"` to find P&L).
- [ ] **Update `nse_cli.spec`** — Include new dependencies and data files for PyInstaller compilation.

### 5.3 Nice-to-have (Medium-term, Future Features)

- [ ] **Fundamental Analysis engine** — DCF calculations, ratio trend analysis, quantitative portfolio construction (as outlined in README).
- [ ] **Web dashboard** — A simple frontend (or Streamlit app) that queries Supabase and displays interactive charts.
- [ ] **Sector classification backfill** — `nse_list.py` sets `sector=""` for all companies. Could be filled from Finology or NSE API data.
- [ ] **Notification system** — Alert on large metric changes (e.g., PE drops 50%+) via email or Telegram.
- [ ] **Remove `schedule` dependency** — It's in `requirements.txt` but unused since CI cron replaced in-process scheduling.
- [ ] **Publish to PyPI** — Package as `nse-fundamentals` for `pip install` distribution.

### 5.4 Known Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Finology changes HTML structure | Medium | High (parsing breaks) | Add header-based table detection instead of index-based |
| Cloudflare upgrades Turnstile | Medium | High (bypass breaks) | SeleniumBase UC mode is actively maintained; monitor releases |
| Supabase free-tier limits hit | Low | Medium (writes throttled) | Keep-alive ping avoids suspension; usage is well within limits |
| GitHub Actions Xvfb compatibility breaks | Low | Medium (CI scrape fails) | `test_bypass.yml` matrix catches this early across OS |
| Git LFS storage quota exceeded | Low | Medium (can't push DB) | Parquet backups reduce size; monitor LFS usage |

---

> **For the next session:** Start with this document. Read sections 5.1–5.2 for immediate action items. Reference section 1 for any architectural questions. The codebase is fully functional for local use; the primary open question is CI reliability for the quarterly scrape workflow.
