# API Query Guide

This guide explains how to use the Supabase Python SDK to query financial data from the **NSE Unified Fundamentals** database.

## 1. Connection Header

Before running any queries, initialize the Supabase client:

```python
from supabase import create_client

SUPABASE_URL = "https://hgakynqiwrzlrtdiepwz.supabase.co"
SUPABASE_KEY = "sb_publishable_GTnOe8ef-eh4eD8DqFk3pA_-NEd34gl"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
```

---

## 2. Querying Companies

### Fetch a single company
```python
res = supabase.table("companies").select("*").eq("symbol", "TCS").execute()
company = res.data[0] if res.data else None
```

### Search companies by name (Partial match)
```python
res = supabase.table("companies").select("symbol, company_name").ilike("company_name", "%Motors%").execute()
```

### Get all companies in a specific sector
```python
res = supabase.table("companies").select("*").eq("sector", "Information Technology").execute()
```

---

## 3. Querying Essentials (KPIs)

The `company_essentials` table contains a snapshot of the most recent key metrics.

### Get key metrics for a symbol
```python
# Fetches Market Cap, PE, ROE, etc.
res = supabase.table("company_essentials").select("*").eq("symbol", "RELIANCE").execute()
```

### Filter for specific metrics across all companies
Find all companies with a P/E ratio less than 15:
```python
res = (
    supabase.table("company_essentials")
    .select("symbol, value_num")
    .eq("metric_name", "p_e")
    .lt("value_num", 15)
    .execute()
)
```

---

## 4. Querying Yearly Financials

Financial statements are stored in the `yearly_financials` table using an EAV (Entity-Attribute-Value) structure.

### Get Profit & Loss for the last 5 years
```python
res = (
    supabase.table("yearly_financials")
    .select("*")
    .eq("symbol", "INFY")
    .eq("source_table", "profit_loss")
    .order("fiscal_year", desc=True)
    .execute()
)
```

### Get Net Profit for multiple companies in 2024
```python
res = (
    supabase.table("yearly_financials")
    .select("symbol, value_num")
    .eq("fiscal_year", 2024)
    .eq("metric_name", "net_profit")
    .in_("symbol", ["RELIANCE", "TCS", "HDFCBANK"])
    .execute()
)
```

---

## 5. Querying Quarterly Results

### Get the most recent quarterly growth
```python
res = (
    supabase.table("quarterly_financials")
    .select("*")
    .eq("symbol", "ZOMATO")
    .order("quarter_date", desc=True)
    .limit(10) # Get all metrics for the latest quarter
    .execute()
)
```

---

## 6. Advanced Tips

### Handling "Crores" in code
Most financial values are stored in **absolute terms** (actual Rupees). To convert them back to "Crores" for display:
```python
def to_crores(value):
    return value / 10**7 if value else 0

total_revenue = to_crores(res.data[0]['value_num'])
print(f"Revenue: ₹{total_revenue:.2f} Cr")
```

### Pagination (Limit & Offset)
If you are building a UI, use `limit` and `range` for pagination:
```python
res = (
    supabase.table("companies")
    .select("symbol, company_name")
    .order("symbol")
    .range(0, 9) # Fetch first 10 rows
    .execute()
)
```

### Counting Results
```python
res = supabase.table("companies").select("*", count="exact").limit(0).execute()
print(f"Total Companies: {res.count}")
```
