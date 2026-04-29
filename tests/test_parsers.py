import sys
import os
import pytest
from pathlib import Path

# Add nse_project to path
sys.path.insert(0, str(Path(__file__).parent.parent / "nse_project"))

from scrapers.parsers import _to_snake, _clean_numeric_string, _parse_value

def test_to_snake():
    assert _to_snake("Net Profit Margin (%)") == "net_profit_margin_pct"
    assert _to_snake("P/E") == "p_e"
    assert _to_snake("Book Value (TTM)") == "book_value_ttm"
    assert _to_snake("ROCE %") == "roce_pct"
    assert _to_snake("Debt to Equity") == "debt_to_equity"

def test_clean_numeric_string():
    assert _clean_numeric_string("5507.82 Cr.") == "5507.82"
    assert _clean_numeric_string("Rs 1,000,000") == "1000000"
    assert _clean_numeric_string("\u20b9 50.5") == "50.5"
    assert _clean_numeric_string("28.80%") == "28.80"
    assert _clean_numeric_string("-") == "-"

def test_parse_value():
    # Value parsing correctly identifies Crores
    assert _parse_value("10 Cr.") == (100000000.0, None)
    
    # Value parsing correctly identifies percentage
    assert _parse_value("50%") == (0.5, None)
    
    # Default to crores flag
    assert _parse_value("10", default_to_crores=True) == (100000000.0, None)
    
    # Default to percentage flag
    assert _parse_value("25", default_to_percentage=True) == (0.25, None)
    
    # Metric key triggers percentage scaling
    assert _parse_value("15", metric_key="roce_pct") == (0.15, None)
    
    # Parsing failures return as text
    assert _parse_value("Some Date") == (None, "Some Date")
    
    # Empty/Null handling
    assert _parse_value("-") == (None, None)
    assert _parse_value("") == (None, None)
    assert _parse_value("N/A") == (None, None)

def test_parse_value_combination():
    # If standard value shouldn't scale
    assert _parse_value("100", default_to_crores=False) == (100.0, None)
    
    # If the metric key is percentage but default_to_crores is passed (e.g., P&L margin)
    assert _parse_value("5", default_to_crores=True, metric_key="margin_pct") == (0.05, None)
