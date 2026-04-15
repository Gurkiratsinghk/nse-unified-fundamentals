import sys
import os

# Add nse_project to path so we can import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.supabase_sync import supabase

def ping_supabase():
    if not supabase:
        print("❌ Supabase credentials not found. Check your environment variables.")
        sys.exit(1)
        
    try:
        # A lightweight query to keep the project active
        # We just fetch the symbol of one company
        res = supabase.table("companies").select("symbol").limit(1).execute()
        print(f"✅ Successfully pinged Supabase! (Found {len(res.data)} records)")
    except Exception as e:
        print(f"❌ Failed to ping Supabase: {e}")
        sys.exit(1)

if __name__ == "__main__":
    ping_supabase()
