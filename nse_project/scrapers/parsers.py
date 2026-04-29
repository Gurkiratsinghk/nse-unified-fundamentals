"""
HTML Parsing utilities for extracting financial data from Data Source.
"""
import re
from loguru import logger
from bs4 import BeautifulSoup

def _to_snake(text: str) -> str:
    """
    Convert a human-readable label to a clean snake_case key.
    e.g. "Net Profit Margin (%)" -> "net_profit_margin_pct"
         "P/E"                   -> "p_e"
         "Book Value (TTM)"      -> "book_value_ttm"
    """
    text = text.strip()
    text = text.replace("%", "pct")
    text = re.sub(r"[^\w\s]", " ", text)       # non-alphanumeric -> space
    text = re.sub(r"\s+", "_", text.strip()).lower()
    text = re.sub(r"_+", "_", text).strip("_") # collapse repeated underscores
    return text or "unknown"

def _clean_numeric_string(raw: str) -> str:
    """
    Strip all unit suffixes and formatting from a raw string, leaving only
    a number string suitable for float() conversion.

    Strips: commas, Rs symbol, 'Cr.', 'Cr', '%', whitespace, non-breaking space.
    Does NOT strip the leading minus sign or decimal point.
    """
    return (
        raw.strip()
           .replace("\xa0", " ")
           .replace(",", "")
           .replace("\u20b9", "")   # rupee sign
           .replace("Rs", "")
           .replace("Cr.", "")
           .replace("Cr",  "")
           .replace("%",   "")
           .strip()
    )

def _parse_value(
    raw: str,
    default_to_crores: bool = False,
    default_to_percentage: bool = False,
    metric_key: str = ""
) -> tuple[float | None, str | None]:
    """
    Parse a raw cell/field string into (value_num, value_text).

    Returns
    -------
    (float, None)  -- successfully parsed to a number
    (None, str)    -- non-numeric text worth preserving (e.g. dates, names)
    (None, None)   -- empty / null marker (dash, blank, N/A)

    Rules
    -----
    - "Cr." suffix is stripped; if detected or flag passed, value is multiplied by 10,000,000.
    - "%" suffix is stripped; if detected or flag passed, value is divided by 100. 
      The metric name signals the unit.
    - "-" or "--" are treated as NULL, not zero.
    """
    if not raw:
        return None, None

    cleaned = _clean_numeric_string(raw)

    if cleaned in ("", "-", "--", "N/A", "NA", "n/a"):
        return None, None

    try:
        val = float(cleaned)
    except ValueError:
        return None, cleaned if cleaned else None
        
    is_crores = "Cr" in raw
    is_percentage = "%" in raw or "pct" in metric_key

    if default_to_crores and not is_crores:
        skip_scaling = (is_percentage or "rs" in metric_key)
        if not skip_scaling:
            is_crores = True

    if default_to_percentage and not is_percentage:
        is_percentage = True

    if is_crores:
        val *= 10000000.0
    elif is_percentage:
        val /= 100.0

    return val, None

def _is_numeric_like(text: str) -> bool:
    """Check if a string looks like a numeric value (e.g. '123_45', '123.45', '123')."""
    cleaned = text.replace("_", "").replace(".", "").replace(",", "").strip()
    return cleaned.isdigit()

def _parse_essentials(soup: BeautifulSoup) -> dict[str, tuple[float | None, str | None]]:
    """
    Extract every labeled metric from the #companyessentials div.
    All values are cleaned and stored as plain floats where possible.
    Returns: { snake_case_label -> (value_num, value_text) }
    """
    results: dict[str, tuple[float | None, str | None]] = {}

    block = soup.find("div", id="companyessentials")
    if not block:
        logger.warning("  #companyessentials div not found on page.")
        return results

    containers = block.find_all(
        "div",
        class_=lambda cls: cls and ("col-" in cls or "mb-" in cls)
    )

    for div in containers:
        label_tag = div.find("small")
        value_tag = div.find("p")

        if not label_tag or not value_tag:
            continue

        label = label_tag.get_text(strip=True)
        value = value_tag.get_text(strip=True)

        # Skip labels that look like numbers (e.g. '126869_54')
        if not label or _is_numeric_like(label):
            if label:
                logger.debug(f"  Skipping numeric-like label: '{label}'")
            continue

        if not value or not _clean_numeric_string(value):
            continue

        key = _to_snake(label)
        if key in results:
            continue  # first occurrence wins

        num, txt = _parse_value(value, metric_key=key)
        results[key] = (num, txt if num is None else None)

    logger.debug(f"  Essentials: {len(results)} clean metrics extracted.")
    return results

def _extract_year_headers(header_cells: list) -> list[int | None]:
    """
    Parse a list of <th>/<td> BeautifulSoup elements into fiscal years.
    "Mar 2021", "FY2021", "2021" -> 2021. Unknown/TTM headers -> None.
    """
    years: list[int | None] = []
    for cell in header_cells:
        text  = cell.get_text(strip=True)
        match = re.search(r"\b(20\d{2}|19\d{2})\b", text)
        years.append(int(match.group()) if match else None)
    return years

def _parse_table(
    table_soup: BeautifulSoup,
    source_label: str,
    known_year_headers: list[int | None] | None = None,
) -> dict[int, dict[str, tuple[float | None, str | None]]]:
    """
    Parse a financial data table into a year-keyed results dict.
    Returns: { fiscal_year (int) -> { snake_case_metric -> (value_num, value_text) } }
    """
    result: dict[int, dict[str, tuple[float | None, str | None]]] = {}
    is_financial_statement = source_label in ("profit_loss", "balance_sheet", "cash_flow")
    is_shareholding = source_label in ("promoter_shareholding", "investor_shareholding")

    all_rows = table_soup.find_all("tr")
    if not all_rows:
        logger.warning(f"  Table '{source_label}': no rows found.")
        return result

    header_cells = all_rows[0].find_all(["th", "td"])
    header_texts = [c.get_text(strip=True) for c in header_cells]

    # Balance Sheet pattern: first row is all-blank except for column 0
    all_blank_after_first = (
        len(header_texts) > 1 and all(h == "" for h in header_texts[1:])
    )

    if all_blank_after_first and known_year_headers:
        year_columns = known_year_headers
    else:
        year_columns = _extract_year_headers(header_cells[1:])

    if not any(year_columns):
        logger.warning(f"  Table '{source_label}': no fiscal years resolved from headers.")
        return result

    for row in all_rows[1:]:
        cells = row.find_all(["th", "td"])
        if not cells:
            continue

        metric_raw = cells[0].get_text(strip=True)
        if not metric_raw:
            continue

        metric_key = _to_snake(metric_raw)

        for col_idx, fiscal_year in enumerate(year_columns):
            if fiscal_year is None:
                continue

            cell_idx = col_idx + 1  # offset: col 0 is always the metric label
            if cell_idx >= len(cells):
                break

            raw_value = cells[cell_idx].get_text(strip=True)
            num, txt  = _parse_value(
                raw_value, 
                default_to_crores=is_financial_statement, 
                default_to_percentage=is_shareholding,
                metric_key=metric_key
            )

            result.setdefault(fiscal_year, {})[metric_key] = (num, txt)

    metrics_sample = len(next(iter(result.values()), {}))
    logger.debug(
        f"  Table '{source_label}': "
        f"{len(result)} years x {metrics_sample} metrics"
    )
    return result

def _parse_quarterly_table(
    table_soup: BeautifulSoup,
    source_label: str = "quarterly_results",
) -> dict[str, dict[str, tuple[float | None, str | None]]]:
    """
    Parse a quarterly financial data table.
    Returns: { quarter_date (str) -> { snake_case_metric -> (value_num, value_text) } }
    """
    result: dict[str, dict[str, tuple[float | None, str | None]]] = {}
    
    all_rows = table_soup.find_all("tr")
    if not all_rows:
        logger.warning(f"  Table '{source_label}': no rows found.")
        return result

    header_cells = all_rows[0].find_all(["th", "td"])
    quarter_columns = [c.get_text(strip=True) for c in header_cells[1:]]

    for row in all_rows[1:]:
        cells = row.find_all(["th", "td"])
        if not cells:
            continue

        metric_raw = cells[0].get_text(strip=True)
        if not metric_raw:
            continue

        metric_key = _to_snake(metric_raw)

        for col_idx, quarter_str in enumerate(quarter_columns):
            if not quarter_str or quarter_str.upper() == "TTM":
                continue

            cell_idx = col_idx + 1
            if cell_idx >= len(cells):
                break

            raw_value = cells[cell_idx].get_text(strip=True)
            num, txt = _parse_value(
                raw_value, 
                default_to_crores=True, 
                metric_key=metric_key
            )

            result.setdefault(quarter_str, {})[metric_key] = (num, txt)

    metrics_sample = len(next(iter(result.values()), {}))
    logger.debug(
        f"  Table '{source_label}': "
        f"{len(result)} quarters x {metrics_sample} metrics"
    )
    return result

def _get_table_by_id(soup: BeautifulSoup, div_id: str) -> BeautifulSoup | None:
    """Find a table inside a parent div identified by ID."""
    div = soup.find("div", id=div_id)
    if div:
        return div.find("table")
    return None

def _parse_all_tables(
    soup: BeautifulSoup,
) -> dict[str, dict[int, dict[str, tuple[float | None, str | None]]]]:
    """
    Extract all financial tables from the page using ID-based detection.
    """
    output: dict[str, dict] = {}

    # Quarterly Results
    q_table = _get_table_by_id(soup, "mainContent_quarterly")
    output["quarterly_results"] = _parse_quarterly_table(q_table, "quarterly_results") if q_table else {}

    # P&L first — needed to derive canonical year header list
    pl_table = _get_table_by_id(soup, "profit")
    if pl_table:
        output["profit_loss"] = _parse_table(pl_table, "profit_loss")
        pl_header_row = pl_table.find_all("tr")[0].find_all(["th", "td"])
        year_header_list = _extract_year_headers(pl_header_row[1:])
    else:
        output["profit_loss"] = {}
        year_header_list = []

    # Balance Sheet — pass year headers from P&L
    bs_table = _get_table_by_id(soup, "balance")
    output["balance_sheet"] = (
        _parse_table(bs_table, "balance_sheet", known_year_headers=year_header_list)
        if bs_table else {}
    )

    # Cash Flow
    cf_table = _get_table_by_id(soup, "mainContent_cashflows")
    output["cash_flow"] = (
        _parse_table(cf_table, "cash_flow", known_year_headers=year_header_list)
        if cf_table else {}
    )

    # Promoter Shareholding
    pr_table = _get_table_by_id(soup, "pills-Promoter")
    output["promoter_shareholding"] = (
        _parse_table(pr_table, "promoter_shareholding")
        if pr_table else {}
    )

    # Investor Shareholding
    inv_table = _get_table_by_id(soup, "pills-Investors")
    output["investor_shareholding"] = (
        _parse_table(inv_table, "investor_shareholding")
        if inv_table else {}
    )

    return output

def parse_company_page(soup: BeautifulSoup) -> dict:
    """Main parsing orchestrator. Returns essentials + all tables."""
    essentials = _parse_essentials(soup)
    tables = _parse_all_tables(soup)
    return {"essentials": essentials, **tables}
