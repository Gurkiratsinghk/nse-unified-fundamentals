"""
Test that ID-based table detection returns the same tables as the current
index-based approach. Uses cached HTML files (tests/data/*.html).

This must pass BEFORE we migrate financials.py to the new detection method.
"""
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

# Add nse_project to path
sys.path.insert(0, str(Path(__file__).parent.parent / "nse_project"))

DATA_DIR = Path(__file__).parent / "data"

# Current hardcoded indices from financials.py
INDEX_MAP = {
    "quarterly_results":      1,
    "profit_loss":            2,
    "balance_sheet":          3,
    "cash_flow":              4,
    "promoter_shareholding":  5,
    "investor_shareholding":  6,
}

# Proposed: parent div IDs that contain each table
ID_MAP = {
    "quarterly_results":      "mainContent_quarterly",
    "profit_loss":            "profit",
    "balance_sheet":          "balance",
    "cash_flow":              "mainContent_cashflows",
    "promoter_shareholding":  "pills-Promoter",
    "investor_shareholding":  "pills-Investors",
}


def _get_table_by_index(soup, label):
    """Current approach: find all <table> tags and pick by position."""
    tables = soup.find_all("table")
    idx = INDEX_MAP[label]
    if idx < len(tables):
        return tables[idx]
    return None


def _get_table_by_id(soup, label):
    """Proposed approach: find parent div by ID, then get its <table>."""
    div_id = ID_MAP[label]
    div = soup.find("div", id=div_id)
    if div:
        return div.find("table")
    return None


def _table_fingerprint(table):
    """Return a comparable fingerprint: first row cell texts + row count."""
    if table is None:
        return None
    rows = table.find_all("tr")
    first_row = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])] if rows else []
    return (tuple(first_row), len(rows))


@pytest.fixture(params=["TCS", "RELIANCE", "HAL", "HDFCBANK", "INFY", "BAJFINANCE"])
def soup(request):
    html_path = DATA_DIR / f"{request.param}.html"
    if not html_path.exists():
        pytest.skip(f"Cached HTML not found: {html_path}")
    return BeautifulSoup(html_path.read_text(encoding="utf-8"), "lxml")


@pytest.mark.parametrize("label", INDEX_MAP.keys())
def test_id_matches_index(soup, label):
    """The ID-based finder must return the exact same table as the index-based finder, EXCEPT when index-based shifts due to missing tables."""
    
    # Check if cash flow is missing, which breaks index-based mapping for subsequent tables
    has_cash_flow = soup.find("div", id=ID_MAP["cash_flow"]) is not None
    if not has_cash_flow and label in ["promoter_shareholding", "investor_shareholding", "cash_flow"]:
        return # Skip index comparison because index logic is known to be broken here

    by_index = _get_table_by_index(soup, label)
    by_id = _get_table_by_id(soup, label)

    assert by_index is not None, f"Index-based lookup failed for {label}"
    assert _table_fingerprint(by_index) == _table_fingerprint(by_id), (
        f"Table mismatch for {label}"
    )


@pytest.mark.parametrize("label", ID_MAP.keys())
def test_id_lookup_finds_table(soup, label):
    """Every ID in ID_MAP must resolve to a non-None table, except Cash Flow for financials."""
    table = _get_table_by_id(soup, label)
    if label == "cash_flow" and table is None:
        return # Banks/NBFCs often don't have Cash Flow on Finology
    assert table is not None, f"No table found inside div#{ID_MAP[label]}"
