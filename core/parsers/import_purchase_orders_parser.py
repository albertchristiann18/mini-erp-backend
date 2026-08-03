"""
Pure parsing functions for the purchase-orders import workbook.

No ORM, no I/O — all functions are testable in isolation with in-memory rows.
Implements:
  - Date extraction from sheet names (bilingual month map)
  - Column detection for per-sheet dynamic layouts
  - Exchange-rate extraction from RMB marker rows
  - SKU validity checks
  - Decimal parsing (never float)
  - Carry-forward price logic
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

MONTH_MAP: dict[str, int] = {
    "januari": 1,
    "january": 1,
    "jan": 1,
    "februari": 2,
    "february": 2,
    "feb": 2,
    "maret": 3,
    "march": 3,
    "mar": 3,
    "april": 4,
    "mei": 5,
    "may": 5,
    "juni": 6,
    "june": 6,
    "juli": 7,
    "july": 7,
    "agustus": 8,
    "august": 8,
    "september": 9,
    "sept": 9,
    "oktober": 10,
    "oct": 10,
    "october": 10,
    "november": 11,
    "nov": 11,
    "desember": 12,
    "december": 12,
    "dec": 12,
}

DATE_PATTERN = re.compile(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})")


@dataclass
class PoLineRow:
    sku_variant_code: str
    order_qty: int
    unit_price_rmb: Decimal  # effective price (disc if > 1.0, else price, else carry-forward)
    stock_on_hand: int | None


@dataclass
class ParsedPoSheet:
    sheet_name: str
    po_date: date
    exchange_rate: int
    rows: list[PoLineRow] = field(default_factory=list)


def parse_po_date(sheet_name: str) -> date | None:
    """Extract a date from a sheet name like 'PO Recap 2 April 2025'."""
    normalized = sheet_name.lower()
    match = DATE_PATTERN.search(normalized)
    if not match:
        return None
    day_str, month_str, year_str = match.group(1), match.group(2), match.group(3)
    month = MONTH_MAP.get(month_str)
    if month is None:
        return None
    try:
        return date(int(year_str), month, int(day_str))
    except ValueError:
        return None


def detect_columns(headers_row: tuple) -> dict[str, int] | None:
    """
    Return a mapping of logical column name -> column index.

    Looks for 'sku' and 'order' (required), plus 'price', 'disc', 'soh' (optional).
    Returns None if either required column is missing.
    """
    col_map: dict[str, int] = {}
    for idx, cell in enumerate(headers_row):
        if cell is None:
            continue
        header = str(cell).strip().lower()
        if "sku" in header and "sku" not in col_map:
            col_map["sku"] = idx
        elif "price" in header and "price" not in col_map:
            col_map["price"] = idx
        elif "disc" in header and "disc" not in col_map:
            col_map["disc"] = idx
        elif "order" in header and "order" not in col_map:
            col_map["order"] = idx
        elif "soh" in header and "soh" not in col_map:
            col_map["soh"] = idx

    if "sku" not in col_map or "order" not in col_map:
        return None
    return col_map


def extract_exchange_rate(row: tuple) -> int | None:
    """
    Find a cell containing 'RMB' (case-insensitive) and return the next numeric cell as int.
    Returns None if not found.
    """
    cells = list(row)
    for i, cell in enumerate(cells):
        if cell is None:
            continue
        if "rmb" in str(cell).lower():
            # Look for the next numeric value
            for j in range(i + 1, len(cells)):
                candidate = cells[j]
                if candidate is None:
                    continue
                try:
                    return int(Decimal(str(candidate)))
                except (InvalidOperation, ValueError):
                    continue
    return None


def is_sku_valid(sku_code: str | None) -> bool:
    """
    Return True if sku_code looks like a real SKU.

    Rejects: blank, purely numeric (with optional dots/dashes), row-header
    strings starting with 'Total', and cells containing '#' (e.g. '#REF!').
    """
    if not sku_code:
        return False
    stripped = str(sku_code).strip()
    if not stripped:
        return False
    if stripped.replace(".", "").replace("-", "").isdigit():
        return False
    if stripped.startswith("Total") or "#" in stripped:
        return False
    return True


def parse_decimal(val: object) -> Decimal | None:
    """
    Parse a value as Decimal. Returns None for blank/None/'#REF!' values.
    Never uses float arithmetic.
    """
    if val is None:
        return None
    str_val = str(val).strip()
    if str_val == "" or str_val == "#REF!":
        return None
    try:
        return Decimal(str_val)
    except InvalidOperation:
        return None


def parse_po_sheet(sheet_name: str, rows: list[tuple]) -> ParsedPoSheet | None:
    """
    Parse a single PO sheet and return a ParsedPoSheet or None if the sheet should be skipped.

    Skips if:
    - Fewer than 4 rows
    - No exchange rate row found (no 'RMB' marker)
    - No column headers detected
    - No valid date in sheet name

    Applies:
    - Carry-forward price: if a row has no price/disc, use previous row's price
    - Disc price wins if disc > Decimal("1.0"), else use price
    - Rows filtered: is_sku_valid + qty > 0 + price > 0
    """
    if len(rows) < 3:
        return None

    po_date = parse_po_date(sheet_name)
    if po_date is None:
        return None

    # Scan for exchange rate row (any row containing 'RMB')
    exchange_rate: int | None = None
    headers_row_idx: int | None = None

    for i, row in enumerate(rows):
        # Check for RMB exchange rate row
        if exchange_rate is None:
            rate = extract_exchange_rate(row)
            if rate is not None:
                exchange_rate = rate

        # Check for column headers (contains 'sku' and 'order')
        if headers_row_idx is None:
            col_map = detect_columns(row)
            if col_map is not None:
                headers_row_idx = i

    if exchange_rate is None:
        return None
    if headers_row_idx is None:
        return None

    col_map = detect_columns(rows[headers_row_idx])
    if col_map is None:
        return None

    sku_col = col_map["sku"]
    order_col = col_map["order"]
    price_col = col_map.get("price")
    disc_col = col_map.get("disc")
    soh_col = col_map.get("soh")

    data_rows = rows[headers_row_idx + 1 :]

    result_rows: list[PoLineRow] = []
    carry_price: Decimal | None = None

    for row in data_rows:
        # Extract SKU
        sku_raw = row[sku_col] if len(row) > sku_col else None
        sku_str = str(sku_raw).strip() if sku_raw is not None else ""

        # Extract order qty
        order_raw = row[order_col] if len(row) > order_col else None
        order_dec = parse_decimal(order_raw)
        if order_dec is None:
            order_qty = 0
        else:
            try:
                order_qty = int(order_dec)
            except (ValueError, TypeError):
                order_qty = 0

        # Extract price and disc
        price: Decimal | None = None
        if price_col is not None:
            price = parse_decimal(row[price_col] if len(row) > price_col else None)

        disc: Decimal | None = None
        if disc_col is not None:
            disc = parse_decimal(row[disc_col] if len(row) > disc_col else None)

        # Determine effective price: disc > 1.0 wins, else use price, else carry-forward
        effective_price: Decimal | None = None
        if disc is not None and disc > Decimal("1.0"):
            effective_price = disc
            carry_price = disc
        elif price is not None and price > Decimal("0"):
            effective_price = price
            carry_price = price
        else:
            effective_price = carry_price

        # Extract SOH
        soh: int | None = None
        if soh_col is not None:
            soh_raw = row[soh_col] if len(row) > soh_col else None
            soh_dec = parse_decimal(soh_raw)
            if soh_dec is not None:
                try:
                    soh = int(soh_dec)
                except (ValueError, TypeError):
                    soh = None

        # Filter: valid SKU, qty > 0, price > 0
        if not is_sku_valid(sku_str):
            continue
        if order_qty <= 0:
            continue
        if effective_price is None or effective_price <= Decimal("0"):
            continue

        result_rows.append(
            PoLineRow(
                sku_variant_code=sku_str,
                order_qty=order_qty,
                unit_price_rmb=effective_price,
                stock_on_hand=soh,
            )
        )

    return ParsedPoSheet(
        sheet_name=sheet_name,
        po_date=po_date,
        exchange_rate=exchange_rate,
        rows=result_rows,
    )
