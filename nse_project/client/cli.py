"""
Interactive CLI for NSE Fundamental Analysis.

Provides a rich, guided terminal interface for users of all skill levels
to explore company financials. Uses Supabase API as the primary source,
falling back to the local SQLite database if offline.

Run:  python -m client.cli
"""

import sys
import os

# Ensure nse_project is on the path when run as a module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import csv
import pandas as pd
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt, Confirm
from rich.text import Text
from rich import box

from data.db import get_db
from data.models import Company, CompanyEssentials, YearlyFinancial, QuarterlyFinancial
from data.supabase_sync import supabase

console = Console()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_crores(value: float | None) -> str:
    """Format a raw number (stored in absolute) back to Crores for display."""
    if value is None:
        return "-"
    cr = value / 1e7
    if abs(cr) >= 1:
        return f"₹{cr:,.2f} Cr"
    return f"₹{value:,.2f}"


def _format_value(value_num: float | None, value_text: str | None, source_table: str = "") -> str:
    """Smart formatting based on context."""
    if value_num is not None:
        is_financial = source_table in ("profit_loss", "balance_sheet", "cash_flow", "quarterly_results")
        if is_financial:
            return _format_crores(value_num)
        # Shareholding percentages (stored as decimals like 0.75)
        if source_table in ("promoter_shareholding", "investor_shareholding"):
            return f"{value_num * 100:.2f}%"
        return f"{value_num:,.2f}"
    if value_text:
        return value_text
    return "-"

def _fetch_supabase(table: str, eq: dict = None, or_: str = None, limit: int = None, order: tuple = None):
    """Helper to query Supabase safely, returns None if offline or failed."""
    if not supabase:
        return None
    try:
        query = supabase.table(table).select("*")
        if eq:
            for k, v in eq.items():
                query = query.eq(k, v)
        if or_:
            query = query.or_(or_)
        if order:
            query = query.order(order[0], desc=order[1])
        if limit:
            query = query.limit(limit)
        res = query.execute()
        return res.data
    except Exception as e:
        console.print(f"[dim]Supabase unreachable ({e}). Falling back to local DB...[/]")
        return None

# ---------------------------------------------------------------------------
# Menu actions
# ---------------------------------------------------------------------------

def search_companies():
    """Search for a company by name or symbol."""
    query = Prompt.ask("[bold cyan]Enter company name or symbol to search[/]")
    if not query.strip():
        return

    results = []
    # 1. Try Supabase
    sb_data = _fetch_supabase("companies", or_=f"symbol.ilike.%{query}%,company_name.ilike.%{query}%", limit=20)
    if sb_data is not None:
        results = sb_data
    else:
        # 2. Local Fallback
        with get_db() as session:
            db_res = (
                session.query(Company)
                .filter((Company.symbol.ilike(f"%{query}%")) | (Company.company_name.ilike(f"%{query}%")))
                .limit(20)
                .all()
            )
            # convert ORM objects to dict
            results = [{"symbol": c.symbol, "company_name": c.company_name, "sector": c.sector, "isin": c.isin} for c in db_res]

    if not results:
        console.print(f"[yellow]No companies found matching '{query}'.[/]")
        return

    table = Table(title=f"Search Results for '{query}'", box=box.ROUNDED, show_lines=True)
    table.add_column("Symbol", style="bold green", min_width=10)
    table.add_column("Company Name", style="white", min_width=30)
    table.add_column("Sector", style="dim")
    table.add_column("ISIN", style="dim")

    for c in results:
        table.add_row(c.get("symbol"), c.get("company_name") or "", c.get("sector") or "", c.get("isin") or "")

    console.print(table)


def view_company_essentials():
    """View company essentials snapshot."""
    symbol = Prompt.ask("[bold cyan]Enter company symbol[/]").strip().upper()
    if not symbol:
        return

    company = None
    essentials = []

    # 1. Try Supabase
    sb_comp = _fetch_supabase("companies", eq={"symbol": symbol})
    if sb_comp is not None and len(sb_comp) > 0:
        company = sb_comp[0]
        sb_ess = _fetch_supabase("company_essentials", eq={"symbol": symbol})
        if sb_ess is not None:
            essentials = sb_ess
    else:
        # 2. Local Fallback
        with get_db() as session:
            c = session.query(Company).filter_by(symbol=symbol).first()
            if c:
                company = {"symbol": c.symbol, "company_name": c.company_name, "sector": c.sector, "industry": c.industry, "isin": c.isin}
                db_ess = session.query(CompanyEssentials).filter_by(company_id=c.id).all()
                essentials = [{"metric_name": e.metric_name, "value_num": e.value_num, "value_text": e.value_text} for e in db_ess]

    if not company:
        console.print(f"[red]Company '{symbol}' not found in database.[/]")
        return

    if not essentials:
        console.print(f"[yellow]No essentials data available for {symbol}.[/]")
        return

    console.print(Panel(
        f"[bold]{company.get('company_name')}[/] ([green]{company.get('symbol')}[/])\n"
        f"Sector: {company.get('sector') or 'N/A'}  |  Industry: {company.get('industry') or 'N/A'}  |  ISIN: {company.get('isin') or 'N/A'}",
        title="Company Profile", border_style="blue"
    ))

    table = Table(title=f"Essentials — {symbol}", box=box.SIMPLE_HEAVY)
    table.add_column("Metric", style="cyan", min_width=25)
    table.add_column("Value", style="bold white", justify="right")

    for e in essentials:
        display_val = _format_value(e.get("value_num"), e.get("value_text"))
        metric_label = e.get("metric_name", "").replace("_", " ").title()
        table.add_row(metric_label, display_val)

    console.print(table)


def view_annual_financials():
    """View annual financial data (P&L, Balance Sheet, Cash Flow)."""
    symbol = Prompt.ask("[bold cyan]Enter company symbol[/]").strip().upper()
    if not symbol:
        return

    source_options = {
        "1": ("profit_loss", "Profit & Loss"),
        "2": ("balance_sheet", "Balance Sheet"),
        "3": ("cash_flow", "Cash Flow Statement"),
        "4": ("promoter_shareholding", "Promoter Shareholding"),
        "5": ("investor_shareholding", "Investor Shareholding"),
    }

    console.print("\n[bold]Select financial statement:[/]")
    for key, (_, label) in source_options.items():
        console.print(f"  [green]{key}[/]. {label}")

    choice = Prompt.ask("Choice", choices=list(source_options.keys()), default="1")
    source_table, source_label = source_options[choice]

    rows = []
    # 1. Try Supabase
    sb_data = _fetch_supabase("yearly_financials", eq={"symbol": symbol, "source_table": source_table}, order=("fiscal_year", False))
    if sb_data is not None:
        rows = sb_data
    else:
        # 2. Local Fallback
        with get_db() as session:
            company = session.query(Company).filter_by(symbol=symbol).first()
            if company:
                db_rows = session.query(YearlyFinancial).filter_by(company_id=company.id, source_table=source_table).order_by(YearlyFinancial.fiscal_year, YearlyFinancial.metric_name).all()
                rows = [{"fiscal_year": r.fiscal_year, "metric_name": r.metric_name, "value_num": r.value_num, "value_text": r.value_text} for r in db_rows]

    if not rows:
        console.print(f"[yellow]No {source_label} data found for {symbol}.[/]")
        return

    years = sorted(set(r["fiscal_year"] for r in rows))
    metrics = {}
    for r in rows:
        label = r["metric_name"].replace("_", " ").title()
        if label not in metrics:
            metrics[label] = {}
        metrics[label][r["fiscal_year"]] = _format_value(r.get("value_num"), r.get("value_text"), source_table)

    table = Table(title=f"{source_label} — {symbol}", box=box.ROUNDED, show_lines=True, row_styles=["", "dim"])
    table.add_column("Metric", style="cyan", min_width=25)
    for yr in years:
        table.add_column(f"FY {yr}", justify="right", min_width=14)

    for metric_label, year_vals in metrics.items():
        row = [metric_label] + [year_vals.get(yr, "-") for yr in years]
        table.add_row(*row)

    console.print(table)


def view_quarterly_financials():
    """View quarterly financial results."""
    symbol = Prompt.ask("[bold cyan]Enter company symbol[/]").strip().upper()
    if not symbol:
        return

    rows = []
    # 1. Try Supabase
    sb_data = _fetch_supabase("quarterly_financials", eq={"symbol": symbol, "source_table": "quarterly_results"})
    if sb_data is not None:
        rows = sb_data
    else:
        # 2. Local Fallback
        with get_db() as session:
            company = session.query(Company).filter_by(symbol=symbol).first()
            if company:
                db_rows = session.query(QuarterlyFinancial).filter_by(company_id=company.id, source_table="quarterly_results").order_by(QuarterlyFinancial.quarter_date, QuarterlyFinancial.metric_name).all()
                rows = [{"quarter_date": r.quarter_date, "metric_name": r.metric_name, "value_num": r.value_num, "value_text": r.value_text} for r in db_rows]

    if not rows:
        console.print(f"[yellow]No quarterly data found for {symbol}.[/]")
        return

    quarters = sorted(set(r["quarter_date"] for r in rows))
    metrics = {}
    for r in rows:
        label = r["metric_name"].replace("_", " ").title()
        if label not in metrics:
            metrics[label] = {}
        metrics[label][r["quarter_date"]] = _format_value(r.get("value_num"), r.get("value_text"), "quarterly_results")

    table = Table(title=f"Quarterly Results — {symbol}", box=box.ROUNDED, show_lines=True, row_styles=["", "dim"])
    table.add_column("Metric", style="cyan", min_width=25)
    for q in quarters:
        table.add_column(q, justify="right", min_width=14)

    for metric_label, q_vals in metrics.items():
        row = [metric_label] + [q_vals.get(q, "-") for q in quarters]
        table.add_row(*row)

    console.print(table)


def list_all_companies():
    """List all companies in the database (local fallback prioritized here to save API limits)."""
    # Prefer local DB for listing all companies to save Supabase API calls
    with get_db() as session:
        total = session.query(Company).count()
        if total == 0:
            console.print("[yellow]Local database is empty. Please run a full scrape.[/]")
            return
            
        page_size = 30
        offset = 0

        while offset < total:
            companies = (
                session.query(Company)
                .order_by(Company.symbol)
                .offset(offset)
                .limit(page_size)
                .all()
            )

            table = Table(
                title=f"All Companies (showing {offset+1}–{min(offset+page_size, total)} of {total})",
                box=box.SIMPLE,
            )
            table.add_column("#", style="dim", width=5)
            table.add_column("Symbol", style="bold green", min_width=10)
            table.add_column("Company Name", min_width=30)

            for i, c in enumerate(companies, start=offset + 1):
                table.add_row(str(i), c.symbol, c.company_name or "")

            console.print(table)
            offset += page_size

            if offset < total:
                if not Confirm.ask(f"Show next {min(page_size, total - offset)} companies?", default=True):
                    break


def show_status():
    """Display a dashboard of the current database and sync status."""
    console.print(Panel("[bold]Database & Sync Status[/]", style="cyan"))
    
    with get_db() as session:
        total_companies = session.query(Company).count()
        
        # Unsynced counts
        unsynced_ess = session.query(CompanyEssentials).filter_by(is_synced=False).count()
        unsynced_yr = session.query(YearlyFinancial).filter_by(is_synced=False).count()
        unsynced_qt = session.query(QuarterlyFinancial).filter_by(is_synced=False).count()
        
        # Latest data
        latest_scrape = session.query(YearlyFinancial.scraped_at).order_by(YearlyFinancial.scraped_at.desc()).first()
        latest_scrape_str = latest_scrape[0].strftime("%Y-%m-%d %H:%M") if latest_scrape else "Never"
    
    # Backup info
    db_path = Path(__file__).parent.parent / "data" / "nse_all_stocks.db"
    backups = list(db_path.parent.glob("backup_*"))
    
    table = Table(box=box.SIMPLE)
    table.add_column("Metric", style="dim")
    table.add_column("Value", style="bold")
    
    table.add_row("Total Companies (Local)", str(total_companies))
    table.add_row("Last Successful Scrape (Local)", latest_scrape_str)
    table.add_row("Unsynced Essentials", f"[yellow]{unsynced_ess}[/]" if unsynced_ess > 0 else "[green]0[/]")
    table.add_row("Unsynced Yearly Data", f"[yellow]{unsynced_yr}[/]" if unsynced_yr > 0 else "[green]0[/]")
    table.add_row("Unsynced Quarterly Data", f"[yellow]{unsynced_qt}[/]" if unsynced_qt > 0 else "[green]0[/]")
    table.add_row("Parquet Backup Folders Found", str(len(backups)))
    
    console.print(table)
    
    if supabase:
        console.print("[green]Supabase Connection: Active (Primary Data Source)[/]")
    else:
        console.print("[yellow]Supabase Connection: Inactive (Using Local SQLite)[/]")


def compare_companies_cli(*args):
    """Compare key metrics for multiple companies side-by-side."""
    if args:
        symbols = [s.strip().upper() for s in args if s.strip()][:5]
    else:
        symbols_raw = Prompt.ask("[bold cyan]Enter symbols to compare (comma-separated, max 5)[/]")
        symbols = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()][:5]
    
    if not symbols:
        return

    metrics_of_interest = ["market_cap", "p_e", "p_b", "face_value", "dividend_yield_pct", "roce_pct"]
    
    table = Table(title="Company Comparison", box=box.ROUNDED, show_lines=True)
    table.add_column("Metric", style="cyan")
    
    comparison_data = {}
    
    for symbol in symbols:
        ess_dict = {}
        
        # 1. Try Supabase
        sb_ess = _fetch_supabase("company_essentials", eq={"symbol": symbol})
        if sb_ess is not None and len(sb_ess) > 0:
            for e in sb_ess:
                ess_dict[e["metric_name"]] = _format_value(e.get("value_num"), e.get("value_text"))
        else:
            # 2. Local Fallback
            with get_db() as session:
                company = session.query(Company).filter_by(symbol=symbol).first()
                if not company:
                    console.print(f"[yellow]Skipping {symbol} (not found)[/]")
                    continue
                essentials = session.query(CompanyEssentials).filter_by(company_id=company.id).all()
                ess_dict = {e.metric_name: _format_value(e.value_num, e.value_text) for e in essentials}
                
        if ess_dict:
            table.add_column(symbol, justify="right", style="bold white")
            comparison_data[symbol] = ess_dict

    for m in metrics_of_interest:
        row = [m.replace("_", " ").title()]
        for symbol in comparison_data:
            row.append(comparison_data[symbol].get(m, "-"))
        table.add_row(*row)

    console.print(table)


def export_data_cli(fmt: str = "CSV", symbol: str = None):
    """Export a company's financial data to CSV."""
    if not symbol:
        symbol = Prompt.ask("[bold cyan]Enter symbol to export[/]").strip().upper()
    if not symbol:
        return
        
    rows = []
    # 1. Try Supabase
    sb_data = _fetch_supabase("yearly_financials", eq={"symbol": symbol})
    if sb_data is not None:
        rows = sb_data
    else:
        # 2. Local Fallback
        with get_db() as session:
            company = session.query(Company).filter_by(symbol=symbol).first()
            if not company:
                console.print(f"[red]Company '{symbol}' not found.[/]")
                return
            db_rows = session.query(YearlyFinancial).filter_by(company_id=company.id).all()
            rows = [{"fiscal_year": r.fiscal_year, "source_table": r.source_table, "metric_name": r.metric_name, "value_num": r.value_num, "value_text": r.value_text} for r in db_rows]
        
    if not rows:
        console.print("[yellow]No data to export.[/]")
        return
        
    df = pd.DataFrame([{
        "Year": r.get("fiscal_year"),
        "Statement": r.get("source_table"),
        "Metric": r.get("metric_name"),
        "Value": r.get("value_num") if r.get("value_num") is not None else r.get("value_text")
    } for r in rows])
    
    df_pivoted = df.pivot_table(index=["Statement", "Metric"], columns="Year", values="Value", aggfunc="first")
    
    export_dir = Path(__file__).parent.parent / "reports"
    export_dir.mkdir(exist_ok=True)
    
    filename = export_dir / f"{symbol}_financials_{datetime.now().strftime('%Y%m%d')}.csv"
    df_pivoted.to_csv(filename)
    console.print(f"[success]Data exported successfully to: [bold]{filename}[/][/]")


# ---------------------------------------------------------------------------
# Main menu loop
# ---------------------------------------------------------------------------

MENU_OPTIONS = {
    "1": ("🔍  Search Companies", search_companies),
    "2": ("📋  List All Companies (Local)", list_all_companies),
    "3": ("📊  Company Essentials", view_company_essentials),
    "4": ("📈  Annual Financials", view_annual_financials),
    "5": ("📅  Quarterly Results", view_quarterly_financials),
    "6": ("⚖️   Compare Companies", compare_companies_cli),
    "7": ("📥  Export to CSV", export_data_cli),
    "8": ("ℹ️   Database Status", show_status),
    "0": ("🚪  Exit", None),
}


def main():
    console.print(Panel(
        "[bold cyan]NSE Fundamental Analysis Tool[/]\n"
        "[dim]Explore financial data for all NSE-listed companies.\n"
        "Data sourced publicly • Stored locally & on Supabase[/]",
        border_style="bright_blue",
        padding=(1, 2),
    ))

    while True:
        console.print("\n[bold]What would you like to do?[/]\n")
        for key, (label, _) in MENU_OPTIONS.items():
            console.print(f"  [green]{key}[/]  {label}")

        choice = Prompt.ask("\n[bold cyan]Select an option[/]", choices=list(MENU_OPTIONS.keys()), default="1")

        if choice == "0":
            console.print("[dim]Goodbye! 👋[/]")
            break

        _, action = MENU_OPTIONS[choice]
        if action:
            console.print()
            try:
                action()
            except Exception as e:
                console.print(f"[red]Error: {e}[/]")


if __name__ == "__main__":
    main()
