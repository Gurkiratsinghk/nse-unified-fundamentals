"""
Interactive CLI for NSE Fundamental Analysis.

Provides a rich, high-contrast, guided terminal interface (Bloomberg-style).
Uses Supabase API as the primary source, falling back to local SQLite if offline.

Run:  python -m client.cli
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import csv
import pandas as pd
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt
from rich.layout import Layout
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
    if value is None:
        return "-"
    cr = value / 1e7
    if abs(cr) >= 1:
        return f"INR {cr:,.2f} Cr"
    return f"INR {value:,.2f}"

def _format_value(value_num: float | None, value_text: str | None, source_table: str = "") -> str:
    if value_num is not None:
        is_financial = source_table in ("profit_loss", "balance_sheet", "cash_flow", "quarterly_results")
        if is_financial:
            return _format_crores(value_num)
        if source_table in ("promoter_shareholding", "investor_shareholding"):
            return f"{value_num * 100:.2f}%"
        return f"{value_num:,.2f}"
    if value_text:
        return value_text
    return "-"

def _fetch_supabase(table: str, eq: dict = None, or_: str = None, limit: int = None, order: tuple = None):
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
        return None

# ---------------------------------------------------------------------------
# UI Components
# ---------------------------------------------------------------------------

def get_status_panel() -> Panel:
    with get_db() as session:
        total_companies = session.query(Company).count()
        latest_scrape = session.query(YearlyFinancial.scraped_at).order_by(YearlyFinancial.scraped_at.desc()).first()
        latest_scrape_str = latest_scrape[0].strftime("%Y-%m-%d %H:%M") if latest_scrape else "Never"
    
    status_text = Text()
    status_text.append(f"TOTAL ENTITIES: {total_companies}\n", style="white")
    status_text.append(f"LAST UPDATE:    {latest_scrape_str}\n", style="white")
    status_text.append(f"SUPABASE:       {'CONNECTED' if supabase else 'OFFLINE (LOCAL MODE)'}", style="bold green" if supabase else "bold yellow")
    
    return Panel(status_text, title="[bold white]SYSTEM STATUS[/]", border_style="white", box=box.SQUARE)

def get_command_panel() -> Panel:
    commands = [
        ("S", "Search"),
        ("L", "List All"),
        ("E", "Essentials"),
        ("A", "Annual Fin"),
        ("Q", "Quarterly Fin"),
        ("C", "Compare"),
        ("X", "Export"),
        ("0", "Quit")
    ]
    
    grid = Table.grid(expand=True, padding=(0, 2))
    grid.add_column()
    grid.add_column()
    grid.add_column()
    grid.add_column()
    
    row = []
    for i, (cmd, desc) in enumerate(commands):
        row.append(Text.assemble(("[", "white"), (cmd, "bold cyan"), ("] ", "white"), (desc, "white")))
        if (i + 1) % 4 == 0:
            grid.add_row(*row)
            row = []
    if row:
        grid.add_row(*row)
        
    return Panel(grid, title="[bold white]COMMAND MENU[/]", border_style="white", box=box.SQUARE)

def build_layout(body_content) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body", ratio=1),
        Layout(name="footer", size=5)
    )
    layout["footer"].split_row(
        Layout(name="status", ratio=1),
        Layout(name="commands", ratio=2)
    )
    
    header_panel = Panel(
        Text("NSE UNIFIED FUNDAMENTALS TERMINAL", justify="center", style="bold white on black"),
        style="white on black",
        box=box.SQUARE
    )
    
    layout["header"].update(header_panel)
    layout["body"].update(Panel(body_content, border_style="white", box=box.SQUARE, title="[bold white]DATA VIEW[/]"))
    layout["status"].update(get_status_panel())
    layout["commands"].update(get_command_panel())
    
    return layout

# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def do_search() -> tuple:
    query = Prompt.ask("[bold cyan]Enter search query (or 'X' to cancel)[/]").strip()
    if query.upper() == "X" or not query:
        return Text("Search cancelled.", style="yellow"), None

    results = []
    sb_data = _fetch_supabase("companies", or_=f"symbol.ilike.%{query}%,company_name.ilike.%{query}%")
    if sb_data is not None:
        results = sb_data
    else:
        with get_db() as session:
            db_res = (
                session.query(Company)
                .filter((Company.symbol.ilike(f"%{query}%")) | (Company.company_name.ilike(f"%{query}%")))
                .all()
            )
            results = [{"symbol": c.symbol, "company_name": c.company_name, "sector": c.sector, "isin": c.isin} for c in db_res]

    if not results:
        return Text(f"No companies found matching '{query}'.", style="yellow"), None

    page_size = 15
    offset = 0
    total = len(results)

    while True:
        chunk = results[offset : offset + page_size]
        table = Table(box=box.SQUARE, show_lines=True, border_style="white", 
                      title=f"SEARCH RESULTS FOR '{query.upper()}' ({offset+1}-{min(offset+page_size, total)} of {total})")
        table.add_column("SYMBOL", style="bold cyan", min_width=10)
        table.add_column("COMPANY NAME", style="white", min_width=30)
        table.add_column("SECTOR", style="white")
        table.add_column("ISIN", style="white")

        for c in chunk:
            table.add_row(c.get("symbol"), c.get("company_name") or "", c.get("sector") or "", c.get("isin") or "")

        console.clear()
        layout = build_layout(table)
        console.print(layout)

        if total <= page_size:
            Prompt.ask("[bold cyan]Press ENTER to return to menu[/]")
            return table, None

        choice = Prompt.ask("[bold cyan]COMMAND (N=Next, P=Previous, X=Menu)[/]", default="N").upper()
        if choice == "N" and offset + page_size < total:
            offset += page_size
        elif choice == "P" and offset - page_size >= 0:
            offset -= page_size
        elif choice == "X":
            return table, None

def do_list_all() -> tuple:
    page_size = 50
    offset = 0
    
    while True:
        with get_db() as session:
            total = session.query(Company).count()
            companies = session.query(Company).order_by(Company.symbol).offset(offset).limit(page_size).all()
            
            if not companies:
                return Text("No more companies to show.", style="yellow"), None
                
            table = Table(box=box.SQUARE, border_style="white", title=f"COMPANIES {offset+1} - {min(offset+page_size, total)} OF {total}")
            table.add_column("SYMBOL", style="bold cyan", min_width=10)
            table.add_column("COMPANY NAME", style="white", min_width=30)

            for c in companies:
                table.add_row(c.symbol, c.company_name or "")

            console.clear()
            layout = build_layout(table)
            console.print(layout)
            
            choice = Prompt.ask("[bold cyan]COMMAND (N=Next, P=Previous, X=Back to Menu)[/]", default="N").upper()
            
            if choice == "N":
                if offset + page_size < total:
                    offset += page_size
            elif choice == "P":
                if offset - page_size >= 0:
                    offset -= page_size
            elif choice == "X":
                return table, None

def do_essentials() -> tuple:
    symbol = Prompt.ask("[bold cyan]Enter company symbol (or 'X' to cancel)[/]").strip().upper()
    if not symbol or symbol == "X":
        return Text("Cancelled.", style="yellow"), None

    company = None
    essentials = []

    sb_comp = _fetch_supabase("companies", eq={"symbol": symbol})
    if sb_comp is not None and len(sb_comp) > 0:
        company = sb_comp[0]
        sb_ess = _fetch_supabase("company_essentials", eq={"symbol": symbol})
        if sb_ess is not None:
            essentials = sb_ess
    else:
        with get_db() as session:
            c = session.query(Company).filter_by(symbol=symbol).first()
            if c:
                company = {"symbol": c.symbol, "company_name": c.company_name, "sector": c.sector, "industry": c.industry, "isin": c.isin}
                db_ess = session.query(CompanyEssentials).filter_by(company_id=c.id).all()
                essentials = [{"metric_name": e.metric_name, "value_num": e.value_num, "value_text": e.value_text} for e in db_ess]

    if not company:
        return Text(f"Company '{symbol}' not found.", style="red"), None

    if not essentials:
        return Text(f"No essentials data available for {symbol}.", style="yellow"), None

    layout = Layout()
    layout.split_column(
        Layout(name="profile", size=4),
        Layout(name="data", ratio=1)
    )
    
    prof_text = (
        f"NAME:     {company.get('company_name')}\n"
        f"SYMBOL:   {company.get('symbol')}\n"
        f"SECTOR:   {company.get('sector') or 'N/A'} | INDUSTRY: {company.get('industry') or 'N/A'} | ISIN: {company.get('isin') or 'N/A'}"
    )
    layout["profile"].update(Panel(prof_text, box=box.SQUARE, border_style="white"))

    table = Table(box=box.SQUARE, border_style="white", expand=True)
    table.add_column("METRIC", style="cyan", min_width=25)
    table.add_column("VALUE", style="bold white", justify="right")

    for e in essentials:
        display_val = _format_value(e.get("value_num"), e.get("value_text"))
        metric_label = e.get("metric_name", "").replace("_", " ").upper()
        table.add_row(metric_label, display_val)

    layout["data"].update(table)
    return layout, None

def do_annual() -> tuple:
    symbol = Prompt.ask("[bold cyan]Enter company symbol (or 'X' to cancel)[/]").strip().upper()
    if not symbol or symbol == "X":
        return Text("Cancelled.", style="yellow"), None

    source_options = {
        "1": ("profit_loss", "PROFIT & LOSS"),
        "2": ("balance_sheet", "BALANCE SHEET"),
        "3": ("cash_flow", "CASH FLOW STATEMENT"),
        "4": ("promoter_shareholding", "PROMOTER SHAREHOLDING"),
        "5": ("investor_shareholding", "INVESTOR SHAREHOLDING"),
    }

    choice = Prompt.ask("[bold cyan]Select statement (1=P&L, 2=BS, 3=CF, 4=Promoter, 5=Investor, X=Cancel)[/]", choices=["1", "2", "3", "4", "5", "X", "x"], default="1").upper()
    if choice == "X":
        return Text("Cancelled.", style="yellow"), None
    source_table, source_label = source_options[choice]

    rows = []
    sb_data = _fetch_supabase("yearly_financials", eq={"symbol": symbol, "source_table": source_table}, order=("fiscal_year", False))
    if sb_data is not None:
        rows = sb_data
    else:
        with get_db() as session:
            company = session.query(Company).filter_by(symbol=symbol).first()
            if company:
                db_rows = session.query(YearlyFinancial).filter_by(company_id=company.id, source_table=source_table).order_by(YearlyFinancial.fiscal_year, YearlyFinancial.metric_name).all()
                rows = [{"fiscal_year": r.fiscal_year, "metric_name": r.metric_name, "value_num": r.value_num, "value_text": r.value_text} for r in db_rows]

    if not rows:
        return Text(f"No {source_label} data found for {symbol}.", style="yellow"), None

    years = sorted(set(r["fiscal_year"] for r in rows))
    metrics = {}
    for r in rows:
        label = r["metric_name"].replace("_", " ").upper()
        if label not in metrics:
            metrics[label] = {}
        metrics[label][r["fiscal_year"]] = _format_value(r.get("value_num"), r.get("value_text"), source_table)

    table = Table(title=f"{source_label} — {symbol}", box=box.SQUARE, show_lines=True, border_style="white")
    table.add_column("METRIC", style="cyan", min_width=25)
    for yr in years:
        table.add_column(f"FY {yr}", justify="right", min_width=14, style="white")

    for metric_label, year_vals in metrics.items():
        row = [metric_label] + [year_vals.get(yr, "-") for yr in years]
        table.add_row(*row)

    return table, None

def do_quarterly() -> tuple:
    symbol = Prompt.ask("[bold cyan]Enter company symbol (or 'X' to cancel)[/]").strip().upper()
    if not symbol or symbol == "X":
        return Text("Cancelled.", style="yellow"), None

    rows = []
    sb_data = _fetch_supabase("quarterly_financials", eq={"symbol": symbol, "source_table": "quarterly_results"})
    if sb_data is not None:
        rows = sb_data
    else:
        with get_db() as session:
            company = session.query(Company).filter_by(symbol=symbol).first()
            if company:
                db_rows = session.query(QuarterlyFinancial).filter_by(company_id=company.id, source_table="quarterly_results").order_by(QuarterlyFinancial.quarter_date, QuarterlyFinancial.metric_name).all()
                rows = [{"quarter_date": r.quarter_date, "metric_name": r.metric_name, "value_num": r.value_num, "value_text": r.value_text} for r in db_rows]

    if not rows:
        return Text(f"No quarterly data found for {symbol}.", style="yellow"), None

    quarters = sorted(set(r["quarter_date"] for r in rows))
    metrics = {}
    for r in rows:
        label = r["metric_name"].replace("_", " ").upper()
        if label not in metrics:
            metrics[label] = {}
        metrics[label][r["quarter_date"]] = _format_value(r.get("value_num"), r.get("value_text"), "quarterly_results")

    table = Table(title=f"QUARTERLY RESULTS — {symbol}", box=box.SQUARE, show_lines=True, border_style="white")
    table.add_column("METRIC", style="cyan", min_width=25)
    for q in quarters:
        table.add_column(q, justify="right", min_width=14, style="white")

    for metric_label, q_vals in metrics.items():
        row = [metric_label] + [q_vals.get(q, "-") for q in quarters]
        table.add_row(*row)

    return table, None

def do_compare() -> tuple:
    symbols_raw = Prompt.ask("[bold cyan]Enter symbols to compare (comma-separated, max 5, or 'X' to cancel)[/]")
    if symbols_raw.upper() == "X":
        return Text("Cancelled.", style="yellow"), None
    symbols = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()][:5]
    if not symbols:
        return Text("No symbols provided.", style="yellow"), None

    metrics_of_interest = ["market_cap", "p_e", "p_b", "face_value", "dividend_yield_pct", "roce_pct"]
    
    table = Table(title="COMPANY COMPARISON", box=box.SQUARE, show_lines=True, border_style="white")
    table.add_column("METRIC", style="cyan")
    
    comparison_data = {}
    
    for symbol in symbols:
        ess_dict = {}
        sb_ess = _fetch_supabase("company_essentials", eq={"symbol": symbol})
        if sb_ess is not None and len(sb_ess) > 0:
            for e in sb_ess:
                ess_dict[e["metric_name"]] = _format_value(e.get("value_num"), e.get("value_text"))
        else:
            with get_db() as session:
                company = session.query(Company).filter_by(symbol=symbol).first()
                if not company:
                    continue
                essentials = session.query(CompanyEssentials).filter_by(company_id=company.id).all()
                ess_dict = {e.metric_name: _format_value(e.value_num, e.value_text) for e in essentials}
                
        if ess_dict:
            table.add_column(symbol, justify="right", style="bold white")
            comparison_data[symbol] = ess_dict

    for m in metrics_of_interest:
        row = [m.replace("_", " ").upper()]
        for symbol in comparison_data:
            row.append(comparison_data[symbol].get(m, "-"))
        table.add_row(*row)

    return table, None

def do_export() -> tuple:
    symbol = Prompt.ask("[bold cyan]Enter symbol to export (or 'X' to cancel)[/]").strip().upper()
    if not symbol or symbol == "X":
        return Text("Cancelled.", style="yellow"), None
        
    rows = []
    sb_data = _fetch_supabase("yearly_financials", eq={"symbol": symbol})
    if sb_data is not None:
        rows = sb_data
    else:
        with get_db() as session:
            company = session.query(Company).filter_by(symbol=symbol).first()
            if not company:
                return Text(f"Company '{symbol}' not found.", style="red"), None
            db_rows = session.query(YearlyFinancial).filter_by(company_id=company.id).all()
            rows = [{"fiscal_year": r.fiscal_year, "source_table": r.source_table, "metric_name": r.metric_name, "value_num": r.value_num, "value_text": r.value_text} for r in db_rows]
        
    if not rows:
        return Text("No data to export.", style="yellow"), None
        
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
    return Text(f"Data exported successfully to: {filename}", style="bold green"), None

# ---------------------------------------------------------------------------
# Main Application Loop
# ---------------------------------------------------------------------------

def main():
    current_view = Text("Welcome to the NSE Unified Fundamentals Terminal.\nSelect an option from the Command Menu to begin.", justify="center", style="white")
    
    while True:
        console.clear()
        layout = build_layout(current_view)
        console.print(layout)
        
        cmd = Prompt.ask("[bold cyan]COMMAND[/]").strip().upper()
        
        if cmd == "0":
            console.clear()
            break
        elif cmd == "S":
            current_view, _ = do_search()
        elif cmd == "L":
            current_view, _ = do_list_all()
        elif cmd == "E":
            current_view, _ = do_essentials()
        elif cmd == "A":
            current_view, _ = do_annual()
        elif cmd == "Q_FIN" or cmd == "Q": # Keep Q for quit, use another shortcut for quarterly, but wait, Q is Quarterly Fin in command panel.
            # Fix conflict: Let's make "Q" Quarterly, and "0" Quit as per panel.
            current_view, _ = do_quarterly()
        elif cmd == "C":
            current_view, _ = do_compare()
        elif cmd == "X":
            current_view, _ = do_export()
        else:
            current_view = Text(f"Unknown command: {cmd}", style="red")

if __name__ == "__main__":
    main()
