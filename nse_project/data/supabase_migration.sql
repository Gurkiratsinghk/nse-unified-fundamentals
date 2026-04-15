-- Supabase SQL migration: Create tables for NSE Fundamentals
-- Run this in your Supabase SQL Editor (Dashboard > SQL Editor)

-- 1. Companies table
CREATE TABLE IF NOT EXISTS companies (
    symbol TEXT PRIMARY KEY,
    company_name TEXT,
    sector TEXT,
    industry TEXT,
    isin TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Company Essentials (snapshot metrics, upserted each run)
CREATE TABLE IF NOT EXISTS company_essentials (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL REFERENCES companies(symbol),
    metric_name TEXT NOT NULL,
    value_num DOUBLE PRECISION,
    value_text TEXT,
    scraped_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (symbol, metric_name)
);

-- 3. Yearly Financials (EAV layout, insert-only)
CREATE TABLE IF NOT EXISTS yearly_financials (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL REFERENCES companies(symbol),
    fiscal_year INTEGER NOT NULL,
    source_table TEXT NOT NULL,   -- profit_loss, balance_sheet, cash_flow, etc.
    metric_name TEXT NOT NULL,
    value_num DOUBLE PRECISION,
    value_text TEXT,
    scraped_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (symbol, fiscal_year, source_table, metric_name)
);

-- 4. Quarterly Financials (EAV layout, insert-only)
CREATE TABLE IF NOT EXISTS quarterly_financials (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL REFERENCES companies(symbol),
    quarter_date TEXT NOT NULL,   -- e.g. "Dec 2025"
    source_table TEXT NOT NULL,   -- "quarterly_results"
    metric_name TEXT NOT NULL,
    value_num DOUBLE PRECISION,
    value_text TEXT,
    scraped_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (symbol, quarter_date, source_table, metric_name)
);

-- Indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_ce_symbol ON company_essentials(symbol);
CREATE INDEX IF NOT EXISTS idx_yf_symbol ON yearly_financials(symbol);
CREATE INDEX IF NOT EXISTS idx_yf_symbol_source ON yearly_financials(symbol, source_table);
CREATE INDEX IF NOT EXISTS idx_qf_symbol ON quarterly_financials(symbol);

-- Enable Row-Level Security (optional, disable for public read)
-- ALTER TABLE companies ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE company_essentials ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE yearly_financials ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE quarterly_financials ENABLE ROW LEVEL SECURITY;

-- Public read policies (uncomment if RLS is enabled)
-- CREATE POLICY "Allow public read" ON companies FOR SELECT USING (true);
-- CREATE POLICY "Allow public read" ON company_essentials FOR SELECT USING (true);
-- CREATE POLICY "Allow public read" ON yearly_financials FOR SELECT USING (true);
-- CREATE POLICY "Allow public read" ON quarterly_financials FOR SELECT USING (true);
