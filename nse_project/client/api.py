import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# For the client, we can use the ANON key if the tables are public,
# or the SERVICE ROLE key if they are private. Assuming key is in .env.
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if SUPABASE_URL and SUPABASE_KEY:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    supabase = None

def get_company_profile(symbol: str) -> dict:
    """Fetches company metadata and essentials."""
    if not supabase:
        raise ValueError("Supabase credentials not found in environment.")
        
    company_res = supabase.table("companies").select("*").eq("symbol", symbol.upper()).execute()
    if not company_res.data:
        return {}
        
    company = company_res.data[0]
    
    essentials_res = supabase.table("company_essentials").select("*").eq("symbol", symbol.upper()).execute()
    company["essentials"] = {row["metric_name"]: (row["value_num"], row["value_text"]) for row in essentials_res.data}
    
    return company

def get_yearly_financials(symbol: str, source_table: str) -> dict:
    """
    Fetches yearly financial data for a specific source_table (e.g. 'profit_loss', 'balance_sheet').
    Returns: { fiscal_year -> { metric_name -> (value_num, value_text) } }
    """
    if not supabase:
        raise ValueError("Supabase credentials not found in environment.")
        
    res = supabase.table("yearly_financials").select("*").eq("symbol", symbol.upper()).eq("source_table", source_table).execute()
    
    data = {}
    for row in res.data:
        year = row["fiscal_year"]
        metric = row["metric_name"]
        
        if year not in data:
            data[year] = {}
            
        data[year][metric] = (row["value_num"], row["value_text"])
        
    return data

def get_quarterly_financials(symbol: str) -> dict:
    """
    Fetches quarterly financial data.
    Returns: { quarter_date -> { metric_name -> (value_num, value_text) } }
    """
    if not supabase:
        raise ValueError("Supabase credentials not found in environment.")
        
    res = supabase.table("quarterly_financials").select("*").eq("symbol", symbol.upper()).execute()
    
    data = {}
    for row in res.data:
        q_date = row["quarter_date"]
        metric = row["metric_name"]
        
        if q_date not in data:
            data[q_date] = {}
            
        data[q_date][metric] = (row["value_num"], row["value_text"])
        
    return data
