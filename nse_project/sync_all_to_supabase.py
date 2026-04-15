"""
Manual Sync Utility: Pushes all unsynced local data to Supabase.

Usage:
    python sync_all_to_supabase.py

This is useful when the automatic sync failed during scraping
(e.g., due to network issues). Run this once you have a stable
connection (e.g., on mobile hotspot).
"""

import sys
import os

# Ensure nse_project is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.db import init_db
from data.supabase_sync import sync_all_pending

if __name__ == "__main__":
    init_db()
    print("Starting manual sync of all pending records to Supabase...")
    result = sync_all_pending(max_attempts=3, retry_interval=30)
    if result:
        print("\n✅ All pending records synced successfully!")
    else:
        print("\n⚠️ Some records could not be synced. Check logs above.")
