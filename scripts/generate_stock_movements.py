"""
generate_stock_movements.py
----------------------------
Reads the Google-Drive inventory-movement export and writes
scripts/import_stock_movements.sql

Run from the mini-erp-backend directory:
    python scripts/generate_stock_movements.py

The source file is the mcp tool-result JSON that contains the markdown
table export from the SoraKids Google Sheets inventory workbook.

Table structure confirmed by balance-chain analysis:
  2025-09  pos 65926–82669   Initial stock / pre-Oct purchase batch
  2025-10  pos 82909–99286   October 2025 master ledger
  2025-11  pos 394–15990     November 2025 master ledger (first sub-table only)
  2025-12  pos 99517–114759  December 2025 master ledger (first sub-table only)
  2026-01  pos 114759–130744 January 2026 (unlabelled, confirmed by chain)
  2026-02  pos 130865–147412 February 2026
  2026-03  pos 147533–162000 March 2026
"""

import hashlib
import json
import os
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
SOURCE_FILE = (
    "/Users/jtf01644/.claude/projects/"
    "-Users-jtf01644-personal-mini-erp-project/"
    "44106850-4c67-4d11-a6b8-5408c76d5634/"
    "tool-results/"
    "mcp-claude_ai_Google_Drive-read_file_content-1779897710156.txt"
)

OUTPUT_FILE = Path(__file__).parent / "import_stock_movements.sql"

COMPANY_ID = "01JSORAKIDS0CMPNY0000001AB"
WAREHOUSE_ID = "01JSORAKIDS0WAREHSE000001A"

# ---------------------------------------------------------------------------
# ID helpers  (must match the other import scripts)
# ---------------------------------------------------------------------------


def make_id(seed: str) -> str:
    h = hashlib.md5(seed.encode()).hexdigest()[:20].upper()
    return "01JSORA" + h[:19]


def variant_id(sku: str) -> str:
    return make_id("VAR-" + sku)


def movement_id(sku: str, month_key: str, mtype: str) -> str:
    return make_id(f"SM-{sku}-{month_key}-{mtype}")


def cogs_id(sku: str, month_key: str) -> str:
    return make_id(f"COGS-{sku}-{month_key}")


# ---------------------------------------------------------------------------
# Numeric parser  (handles  \-123,456  and  empty cells)
# ---------------------------------------------------------------------------


def parse_num(s: str) -> int:
    s = str(s).strip().replace(",", "").replace("\\-", "-").replace("\\", "")
    if not s:
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Table parser
# Columns (0-based after SKU):
#   0 BegQTY  1 BegVal  2 InQTY  3 InVal  4 OutQTY  5 OutVal
#   6 AdjQTY  7 AdjVal  8 AftQTY 9 AftVal
# ---------------------------------------------------------------------------

SKU_PATTERN = re.compile(
    r"\|\s*[^|]+\|\s*\d+\s*\|\s*[^|]+\|\s*"  # Desc | Size | Color |
    r"((?:[A-Z]{2,4}-\d{3}-\d{3}-[A-Z]+))\s*\|"  # SKU
    r"\s*([^|]*)\|\s*([^|]*)\|"  # BegQTY | BegVal
    r"\s*([^|]*)\|\s*([^|]*)\|"  # InQTY  | InVal
    r"\s*([^|]*)\|\s*([^|]*)\|"  # OutQTY | OutVal
    r"\s*([^|]*)\|\s*([^|]*)\|"  # AdjQTY | AdjVal
    r"\s*([^|]*)\|\s*([^|]*)\|",  # AftQTY | AftVal
    re.MULTILINE,
)


def parse_table(text: str) -> dict:
    """Return {sku: (bq,bv,iq,iv,oq,ov,aq,av,aftq,aftv)}"""
    rows: dict = {}
    for m in SKU_PATTERN.finditer(text):
        sku = m.group(1).strip()
        vals = tuple(parse_num(m.group(i)) for i in range(2, 12))
        rows[sku] = vals
    return rows


# ---------------------------------------------------------------------------
# Monthly slice definitions  (start / end byte offsets in the raw content)
# Confirmed by DRS-008-100-BLU balance chain:
#   2025-09 After(33) → 2025-10 Beg(33) → … → 2026-03 After(36)  ✓
# ---------------------------------------------------------------------------
MONTH_SLICES = [
    ("2025-09", "2025-09-01", 65926, 82669),
    ("2025-10", "2025-10-01", 82909, 99286),
    ("2025-11", "2025-11-01", 394, 15990),
    ("2025-12", "2025-12-01", 99517, 114759),
    ("2026-01", "2026-01-01", 114759, 130744),
    ("2026-02", "2026-02-01", 130865, 147412),
    ("2026-03", "2026-03-01", 147533, 162000),
]

# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------


def q(val) -> str:
    """Quote a string for SQL, or return NULL."""
    if val is None:
        return "NULL"
    return "'" + str(val).replace("'", "''") + "'"


def ts(date_str: str) -> str:
    """Turn 'YYYY-MM-DD' into a timestamptz literal at midnight Jakarta (UTC+7)."""
    return f"'{date_str} 00:00:00+07'"


# ---------------------------------------------------------------------------
# Main generation
# ---------------------------------------------------------------------------


def generate() -> None:
    # Load source
    with open(SOURCE_FILE) as f:
        raw = json.load(f)
    content: str = raw["fileContent"]

    # Parse all months
    monthly: dict = {}
    for mk, cdate, s, e in MONTH_SLICES:
        monthly[mk] = (cdate, parse_table(content[s:e]))

    # Ordered month keys (chronological)
    month_keys = [mk for mk, *_ in MONTH_SLICES]

    # Track first occurrence of each SKU (for beginning-balance ADJ)
    first_month: dict[str, str] = {}
    for mk in month_keys:
        _, rows = monthly[mk]
        for sku in rows:
            if sku not in first_month:
                first_month[sku] = mk

    # Collect all unique SKUs
    all_skus: set[str] = set()
    for mk in month_keys:
        all_skus |= set(monthly[mk][1].keys())

    # -----------------------------------------------------------------------
    # Build SQL lines
    # -----------------------------------------------------------------------
    movement_inserts: list[str] = []
    cogs_inserts: list[str] = []
    seen_ids: set[str] = set()

    def add_movement(mid, variant, mtype, qty, ref, note, bb, ba, cdate_str):
        if mid in seen_ids:
            return
        seen_ids.add(mid)
        now = ts(cdate_str)
        movement_inserts.append(
            f"({q(mid)},{q(COMPANY_ID)},{q(variant)},{q(WAREHOUSE_ID)},"
            f"{q(mtype)},'',"
            f"{qty},{q(ref)},{q(note)},"
            f"{bb},{ba},"
            f"{now},{now})"
        )

    def add_cogs(cid, variant, ref, cdate_str, unit_cogs, qty):
        if cid in seen_ids:
            return
        seen_ids.add(cid)
        now = ts(cdate_str)
        cogs_inserts.append(
            f"({q(cid)},{q(COMPANY_ID)},{q(variant)},{q(WAREHOUSE_ID)},"
            f"{q(ref)},{q(cdate_str[:10])},"
            f"2400,{unit_cogs},0,0,"
            f"{qty},{qty},"
            f"{now},{now})"
        )

    for mk in month_keys:
        cdate, rows = monthly[mk]
        month_label = mk  # e.g. "2025-09"

        for sku, vals in rows.items():
            bq, bv, iq, iv, oq, ov, aq, av, aftq, aftv = vals
            vid = variant_id(sku)

            # Skip entirely-zero rows
            if bq == 0 and iq == 0 and oq == 0 and aq == 0 and aftq == 0:
                continue

            # -----------------------------------------------------------------
            # 1. Beginning balance  (only for the FIRST month this SKU appears,
            #    and only when Beginning QTY > 0)
            # -----------------------------------------------------------------
            if mk == first_month.get(sku) and bq > 0:
                mid = movement_id(sku, mk, "BEG")
                add_movement(
                    mid=mid,
                    variant=vid,
                    mtype="ADJ",
                    qty=bq,
                    ref=f"IMPORT-BEGINNING-{month_label}",
                    note=f"Opening balance import for {month_label}",
                    bb=0,
                    ba=bq,
                    cdate_str=cdate,
                )

            # Balance cursor after beginning
            bal_after_beg = bq  # this is the balance before any in/out/adj this month

            # -----------------------------------------------------------------
            # 2. Inbound / Purchase  (if In QTY > 0)
            # -----------------------------------------------------------------
            if iq > 0:
                mid = movement_id(sku, mk, "PUR")
                bb = bal_after_beg
                ba = bb + iq
                add_movement(
                    mid=mid,
                    variant=vid,
                    mtype="PUR",
                    qty=iq,
                    ref=f"IMPORT-IN-{month_label}",
                    note=f"Purchase inbound import for {month_label}",
                    bb=bb,
                    ba=ba,
                    cdate_str=cdate,
                )
                bal_after_in = ba

                # COGS layer for this purchase
                unit_cogs = round(iv / iq) if iq > 0 else 0
                cid = cogs_id(sku, mk)
                add_cogs(
                    cid=cid,
                    variant=vid,
                    ref=f"IMPORT-IN-{month_label}",
                    cdate_str=cdate,
                    unit_cogs=unit_cogs,
                    qty=iq,
                )
            else:
                bal_after_in = bal_after_beg

            # -----------------------------------------------------------------
            # 3. Outbound / Sales  (if Out QTY > 0)
            # -----------------------------------------------------------------
            if oq > 0:
                mid = movement_id(sku, mk, "OUT")
                bb = bal_after_in
                ba = bb - oq
                add_movement(
                    mid=mid,
                    variant=vid,
                    mtype="OUT",
                    qty=-oq,  # negative quantity for outbound
                    ref=f"IMPORT-OUT-{month_label}",
                    note=f"Sales outbound import for {month_label}",
                    bb=bb,
                    ba=ba,
                    cdate_str=cdate,
                )
                bal_after_out = ba
            else:
                bal_after_out = bal_after_in

            # -----------------------------------------------------------------
            # 4. Adjustment  (if Adj QTY != 0)
            # -----------------------------------------------------------------
            if aq != 0:
                mid = movement_id(sku, mk, "ADJ")
                bb = bal_after_out
                ba = aftq  # After QTY is the authoritative final balance
                add_movement(
                    mid=mid,
                    variant=vid,
                    mtype="ADJ",
                    qty=aq,
                    ref=f"IMPORT-ADJ-{month_label}",
                    note=f"Inventory adjustment import for {month_label}",
                    bb=bb,
                    ba=ba,
                    cdate_str=cdate,
                )

    # -----------------------------------------------------------------------
    # Write SQL file
    # -----------------------------------------------------------------------
    lines = []
    lines.append("-- =============================================================")
    lines.append("-- import_stock_movements.sql")
    lines.append("-- Auto-generated by scripts/generate_stock_movements.py")
    lines.append("-- SoraKids inventory movement import: Sep 2025 – Mar 2026")
    lines.append("-- =============================================================")
    lines.append("")
    lines.append("BEGIN;")
    lines.append("")

    # StockMovement inserts
    lines.append("-- -----------------------------------------------------------")
    lines.append("-- inventory_stockmovement")
    lines.append("-- -----------------------------------------------------------")
    lines.append("INSERT INTO inventory_stockmovement")
    lines.append("  (id, company_id, product_variant_id, warehouse_id,")
    lines.append("   movement_type, field_change,")
    lines.append("   quantity, reference_number, note,")
    lines.append("   balance_before, balance_after,")
    lines.append("   cdate, udate)")
    lines.append("VALUES")
    for i, row in enumerate(movement_inserts):
        comma = "," if i < len(movement_inserts) - 1 else ""
        lines.append(f"  {row}{comma}")
    lines.append("ON CONFLICT (id) DO NOTHING;")
    lines.append("")

    # COGS inserts
    lines.append("-- -----------------------------------------------------------")
    lines.append("-- inventory_productcogs")
    lines.append("-- -----------------------------------------------------------")
    lines.append("INSERT INTO inventory_productcogs")
    lines.append("  (id, company_id, product_variant_id, warehouse_id,")
    lines.append("   reference_number, purchase_date, exchange_rate,")
    lines.append("   cogs_amount, allocated_shipping_fee, allocated_delivery_fee,")
    lines.append("   original_qty, remaining_qty,")
    lines.append("   cdate, udate)")
    lines.append("VALUES")
    for i, row in enumerate(cogs_inserts):
        comma = "," if i < len(cogs_inserts) - 1 else ""
        lines.append(f"  {row}{comma}")
    lines.append("ON CONFLICT (id) DO NOTHING;")
    lines.append("")
    lines.append("COMMIT;")
    lines.append("")

    output = "\n".join(lines)
    OUTPUT_FILE.write_text(output, encoding="utf-8")

    print(f"Written: {OUTPUT_FILE}")
    print(f"  StockMovement rows : {len(movement_inserts)}")
    print(f"  ProductCOGS rows   : {len(cogs_inserts)}")


if __name__ == "__main__":
    generate()
