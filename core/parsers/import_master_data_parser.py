"""
Pure parsing functions for the master-data import workbook.

No ORM, no I/O — all functions are testable in isolation with in-memory rows.
Column indices match the Excel sheet layout used by the old raw-psycopg script.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass
class MasterSkuRow:
    sku_code: str
    category_code: str
    product_name: str
    supplier_name: str


@dataclass
class VariantRow:
    sku_code: str
    sku_variant_code: str
    color_code: str
    product_name: str
    supplier_name: str
    color_display: str
    size_code: str
    cogs: Decimal
    base_price: Decimal
    stock_qty: int


def _parse_decimal(raw: object, field_name: str) -> Decimal:
    if raw is None:
        return Decimal("0")
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        raise ValueError(f"Cannot parse {field_name!r} as Decimal: {raw!r}")


def _parse_size_code(raw: object) -> str:
    if raw is None:
        return ""
    if isinstance(raw, float) and raw == int(raw):
        return str(int(raw))
    return str(raw).strip()


def parse_master_sku_sheet(rows: list[tuple]) -> list[MasterSkuRow]:
    """
    Parse the 'Master SKU' sheet rows (skip the header row at index 0).

    Column layout (0-indexed):
      col[4] = supplier_name
      col[5] = category_code
      col[7] = sku_code
      col[9] = product_name

    Rows with a blank sku_code are silently skipped.
    """
    result: list[MasterSkuRow] = []
    for row in rows[1:]:
        sku_code_raw = row[7] if len(row) > 7 else None
        if not sku_code_raw or str(sku_code_raw).strip() == "":
            continue
        sku_code = str(sku_code_raw).strip()
        category_code = str(row[5]).strip() if len(row) > 5 and row[5] else ""
        product_name = str(row[9]).strip() if len(row) > 9 and row[9] else ""
        supplier_name = str(row[4]).strip() if len(row) > 4 and row[4] else ""
        result.append(
            MasterSkuRow(
                sku_code=sku_code,
                category_code=category_code,
                product_name=product_name,
                supplier_name=supplier_name,
            )
        )
    return result


def parse_variant_sheet(rows: list[tuple]) -> list[VariantRow]:
    """
    Parse the 'Master SKU Variant' sheet rows (skip the header row at index 0).

    Column layout (0-indexed):
      col[2]  = color_code
      col[5]  = sku_code
      col[6]  = sku_variant_code
      col[7]  = product_name
      col[8]  = supplier_name
      col[9]  = color_display
      col[12] = order_size (size_code)
      col[13] = cogs (Decimal)
      col[19] = base_price (Decimal)
      col[27] = stock_qty (int)

    Rows with a blank sku_code or blank sku_variant_code are silently skipped.
    Malformed money fields (cogs, base_price) raise ValueError.
    """
    result: list[VariantRow] = []
    for row in rows[1:]:
        sku_variant_code_raw = row[6] if len(row) > 6 else None
        if not sku_variant_code_raw or str(sku_variant_code_raw).strip() == "":
            continue

        sku_code_raw = row[5] if len(row) > 5 else None
        if not sku_code_raw or str(sku_code_raw).strip() == "":
            continue

        sku_code = str(sku_code_raw).strip()
        sku_variant_code = str(sku_variant_code_raw).strip()
        color_code = str(row[2]).strip().lower() if len(row) > 2 and row[2] else ""
        product_name = str(row[7]).strip() if len(row) > 7 and row[7] else ""
        supplier_name = str(row[8]).strip() if len(row) > 8 and row[8] else ""
        color_display = str(row[9]).strip() if len(row) > 9 and row[9] else ""
        size_code = _parse_size_code(row[12] if len(row) > 12 else None)
        cogs = _parse_decimal(row[13] if len(row) > 13 else None, "cogs")
        base_price = _parse_decimal(row[19] if len(row) > 19 else None, "base_price")
        stock_qty_raw = row[27] if len(row) > 27 else None
        if stock_qty_raw is None:
            stock_qty = 0
        else:
            try:
                stock_qty = int(stock_qty_raw)
            except (ValueError, TypeError):
                raise ValueError(f"Cannot parse 'stock_qty' as int: {stock_qty_raw!r}")

        result.append(
            VariantRow(
                sku_code=sku_code,
                sku_variant_code=sku_variant_code,
                color_code=color_code,
                product_name=product_name,
                supplier_name=supplier_name,
                color_display=color_display,
                size_code=size_code,
                cogs=cogs,
                base_price=base_price,
                stock_qty=stock_qty,
            )
        )
    return result


def build_variant_options(
    variant_list: list[VariantRow],
) -> tuple[list[dict], list[str], list[str]]:
    """
    Build variant_options, dim1_options, dim2_options from a list of VariantRow objects.

    dim1 = size (sorted numerically where possible)
    dim2 = color (sorted alphabetically by color_code)

    Returns: (variant_options, dim1_options, dim2_options)
    """
    sizes_seen: list[str] = []
    size_set: set[str] = set()
    color_map: dict[str, str] = {}

    for v in variant_list:
        if v.size_code and v.size_code not in size_set:
            sizes_seen.append(v.size_code)
            size_set.add(v.size_code)
        if v.color_code and v.color_code not in color_map:
            color_map[v.color_code] = v.color_display

    def size_sort_key(s: str) -> tuple[int, int | str]:
        try:
            return (0, int(s))
        except (ValueError, TypeError):
            return (1, s)

    dim1_options = sorted(sizes_seen, key=size_sort_key)
    dim2_options = sorted(color_map.keys())

    variant_options: list[dict] = [
        {
            "id": "size",
            "name": "Size",
            "order": 1,
            "values": [{"id": s, "label": s} for s in dim1_options],
        },
        {
            "id": "color",
            "name": "Color",
            "order": 2,
            "values": [{"id": cc, "label": color_map[cc]} for cc in dim2_options],
        },
    ]
    return variant_options, dim1_options, dim2_options
