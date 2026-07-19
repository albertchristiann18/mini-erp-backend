"""
Pure parsing functions for the sales orders import workbook.

No ORM, no I/O — all functions are testable in isolation with in-memory rows.
Implements:
  - Column detection for per-sheet dynamic layouts
  - Order date parsing (datetime object or string formats)
  - Decimal parsing (never float)
  - Grouping rows by nomor_paket into ParsedSalesOrder objects
"""

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

STATUS_MAPPING: dict[str, str] = {
    "Completed": "COMPLETED",
    "Delivered": "DELIVERED",
    "In_Delivery": "SHIPPING",
    "Processed": "CONFIRMED",
    "Returns": "RETURNED",
    "To_Process": "PENDING",
    "Cancellations": "CANCELLED",
}

STOCK_AFFECTING_STATUSES: frozenset[str] = frozenset(
    {"COMPLETED", "DELIVERED", "SHIPPING", "CONFIRMED"}
)

REQUIRED_HEADER_MAP: dict[str, str] = {
    "nomor_paket_col": "nomor paket",
    "status_col": "status pesanan",
    "order_date_col": "tanggal pesanan dibuat",
    "sku_master_col": "sku master",
    "sku_marketplace_col": "sku marketplace",
    "qty_col": "jumlah",
    "selling_price_col": "harga dibayar",
    "subtotal_col": "subtotal pesanan",
    "customer_col": "nama pembeli",
}

OPTIONAL_HEADER_KEYS: dict[str, str] = {
    "courier_col": "kurir",
    "tracking_col": "nomor awb/resi",
}


@dataclass
class ParsedSalesItem:
    sku_code: str
    quantity: int
    selling_price: int  # Decimal parsed → int (BigIntegerField stores IDR amounts)
    line_total: int


@dataclass
class ParsedSalesOrder:
    order_number: str
    status: str
    is_stock_affecting: bool
    order_date: datetime
    customer_name: str
    courier_name: str
    tracking_number: str
    subtotal: int
    net_revenue: int
    sheet_name: str
    items: list[ParsedSalesItem] = field(default_factory=list)


def clean_header(val: object) -> str:
    """Normalise a header cell value: strip, collapse newlines, lowercase."""
    if val is None:
        return ""
    return str(val).strip().replace("\n", " ").lower()


def detect_columns(headers_row: tuple) -> dict[str, int] | None:
    """
    Return a mapping of logical column name → column index.

    Returns None if any required column from REQUIRED_HEADER_MAP is missing
    (with the exception that only one of sku_master_col/sku_marketplace_col is needed).
    """
    cols: dict[str, int] = {}
    net_rev_col: int | None = None

    for idx, val in enumerate(headers_row):
        key = clean_header(val)
        if not key:
            continue

        for col_name, header_text in REQUIRED_HEADER_MAP.items():
            if key == header_text and col_name not in cols:
                cols[col_name] = idx

        for col_name, header_text in OPTIONAL_HEADER_KEYS.items():
            if key == header_text and col_name not in cols:
                cols[col_name] = idx

        if "total penjualan" in key and net_rev_col is None:
            net_rev_col = idx

    if net_rev_col is not None:
        cols["net_rev_col"] = net_rev_col

    # Check required columns — sku_master_col and sku_marketplace_col are collectively required
    sku_master_present = "sku_master_col" in cols
    sku_marketplace_present = "sku_marketplace_col" in cols

    for col_name in REQUIRED_HEADER_MAP:
        if col_name in ("sku_master_col", "sku_marketplace_col"):
            continue
        if col_name not in cols:
            return None

    if not sku_master_present and not sku_marketplace_present:
        return None

    return cols


def parse_order_date(val: object) -> datetime | None:
    """Parse a cell value to a timezone-aware datetime. Handles datetime objects and strings."""
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val
    if isinstance(val, str):
        stripped = val.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(stripped, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def parse_decimal(val: object) -> Decimal | None:
    """
    Parse a value as Decimal. Returns None for blank/None values.
    Never uses float arithmetic.
    """
    if val is None:
        return None
    str_val = str(val).strip()
    if str_val == "":
        return None
    try:
        return Decimal(str_val)
    except InvalidOperation:
        return None


def parse_sales_sheet(sheet_name: str, rows: list[tuple]) -> list[ParsedSalesOrder]:
    """
    Parse a single sales sheet and return a list of ParsedSalesOrder objects.

    Returns an empty list if:
    - Fewer than 2 rows
    - Required columns are missing

    Rows are grouped by nomor_paket. First row of each group provides order-level
    fields (status, order_date, customer, courier, tracking, subtotal, net_revenue).
    All rows in the group are candidates for items (filtered for valid SKU and qty > 0).
    """
    if len(rows) < 2:
        return []

    headers_row = rows[0]
    cols = detect_columns(headers_row)
    if cols is None:
        return []

    nomor_paket_col = cols["nomor_paket_col"]
    status_col = cols["status_col"]
    order_date_col = cols["order_date_col"]
    sku_master_col: int | None = cols.get("sku_master_col")
    sku_marketplace_col: int | None = cols.get("sku_marketplace_col")
    qty_col = cols["qty_col"]
    selling_price_col = cols["selling_price_col"]
    subtotal_col_idx = cols["subtotal_col"]
    customer_col = cols["customer_col"]
    courier_col: int | None = cols.get("courier_col")
    tracking_col: int | None = cols.get("tracking_col")
    net_rev_col: int | None = cols.get("net_rev_col")

    # Group data rows by nomor_paket (preserve insertion order)
    groups: OrderedDict[str, list[tuple]] = OrderedDict()
    for data_row in rows[1:]:
        if len(data_row) <= max(nomor_paket_col, status_col):
            continue
        np_val = data_row[nomor_paket_col]
        if np_val is None:
            continue
        np_str = str(np_val).strip()
        if not np_str:
            continue
        groups.setdefault(np_str, []).append(data_row)

    results: list[ParsedSalesOrder] = []

    for nomor_paket, group_rows in groups.items():
        first_row = group_rows[0]

        # --- Status ---
        raw_status = str(first_row[status_col]).strip() if first_row[status_col] is not None else ""
        mapped_status = STATUS_MAPPING.get(raw_status, "PENDING")
        is_stock_affecting = mapped_status in STOCK_AFFECTING_STATUSES

        # --- Order date: try first row, fall back to other rows, then now() ---
        order_date = parse_order_date(first_row[order_date_col])
        if order_date is None:
            for r in group_rows:
                if r[order_date_col] is not None:
                    order_date = parse_order_date(r[order_date_col])
                    if order_date is not None:
                        break
        if order_date is None:
            order_date = datetime.now(timezone.utc)

        # --- Customer ---
        customer = (
            str(first_row[customer_col]).strip()[:255]
            if first_row[customer_col] is not None
            else ""
        )

        # --- Courier ---
        courier = ""
        if (
            courier_col is not None
            and courier_col < len(first_row)
            and first_row[courier_col] is not None
        ):
            courier = str(first_row[courier_col]).strip()[:100]

        # --- Tracking ---
        tracking = ""
        if (
            tracking_col is not None
            and tracking_col < len(first_row)
            and first_row[tracking_col] is not None
        ):
            tracking = str(first_row[tracking_col]).strip()[:255]

        # --- Items ---
        items: list[ParsedSalesItem] = []
        for item_row in group_rows:
            # SKU code: try master first, fall back to marketplace
            sku_code: str | None = None
            if sku_master_col is not None and sku_master_col < len(item_row):
                raw_sku = item_row[sku_master_col]
                if raw_sku is not None and str(raw_sku).strip():
                    sku_code = str(raw_sku).strip()
            if sku_code is None:
                if sku_marketplace_col is not None and sku_marketplace_col < len(item_row):
                    raw_sku = item_row[sku_marketplace_col]
                    if raw_sku is not None and str(raw_sku).strip():
                        sku_code = str(raw_sku).strip()
            if sku_code is None:
                continue

            # Quantity
            raw_qty_val = item_row[qty_col] if qty_col < len(item_row) else None
            qty_dec = parse_decimal(raw_qty_val)
            qty = int(qty_dec) if qty_dec is not None else 1
            if qty <= 0:
                continue

            # Selling price
            raw_sp_val = item_row[selling_price_col] if selling_price_col < len(item_row) else None
            sp_dec = parse_decimal(raw_sp_val)
            selling_price = int(sp_dec) if sp_dec is not None else 0

            line_total = selling_price * qty

            items.append(
                ParsedSalesItem(
                    sku_code=sku_code,
                    quantity=qty,
                    selling_price=selling_price,
                    line_total=line_total,
                )
            )

        # --- Subtotal ---
        subtotal: int | None = None
        for r in group_rows:
            if subtotal_col_idx < len(r) and r[subtotal_col_idx] is not None:
                raw = parse_decimal(r[subtotal_col_idx])
                if raw is not None:
                    subtotal = int(raw)
                    break
        if subtotal is None:
            subtotal = sum(it.line_total for it in items)

        # --- Net revenue ---
        net_revenue = 0
        if net_rev_col is not None:
            for r in group_rows:
                if net_rev_col < len(r) and r[net_rev_col] is not None:
                    raw = parse_decimal(r[net_rev_col])
                    if raw is not None:
                        net_revenue = int(raw)
                        break

        results.append(
            ParsedSalesOrder(
                order_number=nomor_paket,
                status=mapped_status,
                is_stock_affecting=is_stock_affecting,
                order_date=order_date,
                customer_name=customer,
                courier_name=courier,
                tracking_number=tracking,
                subtotal=subtotal,
                net_revenue=net_revenue,
                sheet_name=sheet_name,
                items=items,
            )
        )

    return results
