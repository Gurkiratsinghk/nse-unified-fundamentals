import csv
import io
import requests
from loguru import logger
from data.db import get_db
from data.models import Company

NSE_EQUITY_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

def fetch_nse_equities():
    """Fetches the latest active equities from NSE and upserts into the companies table."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    
    logger.info("Fetching EQUITY_L.csv from NSE...")
    try:
        response = requests.get(NSE_EQUITY_URL, headers=headers, timeout=20)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch NSE equities list: {e}")
        return False
        
    csv_text = response.text
    reader = csv.DictReader(io.StringIO(csv_text))
    
    # Standard header names in EQUITY_L.csv
    # SYMBOL,NAME OF COMPANY,SERIES,DATE OF LISTING,PAID UP VALUE,MARKET LOT,ISIN NUMBER,FACE VALUE
    
    inserted = 0
    updated = 0
    
    with get_db() as session:
        for row in reader:
            # Handle potential leading/trailing spaces in header names and values
            row = {k.strip(): v.strip() if v else "" for k, v in row.items()}
            
            series = row.get("SERIES", "").upper()
            # Only track the main equity series (EQ)
            if series != "EQ":
                continue
                
            symbol = row.get("SYMBOL", "")
            name = row.get("NAME OF COMPANY", "")
            isin = row.get("ISIN NUMBER", "")
            
            if not symbol:
                continue
                
            company = session.query(Company).filter_by(symbol=symbol).first()
            if not company:
                session.add(Company(
                    symbol=symbol,
                    company_name=name,
                    isin=isin,
                    sector="",  # Will be filled later if needed
                    industry=""
                ))
                inserted += 1
            else:
                # Update name and ISIN if they changed
                if company.company_name != name or company.isin != isin:
                    company.company_name = name
                    company.isin = isin
                    updated += 1
                    
        session.commit()
    
    logger.success(f"NSE equities list updated. {inserted} inserted, {updated} updated.")
    return True

if __name__ == "__main__":
    fetch_nse_equities()
