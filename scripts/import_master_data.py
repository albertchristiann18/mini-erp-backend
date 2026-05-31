#!/usr/bin/env python3
"""
Mirako Kids ERP — master data import.

Run from mini-erp-backend/:
    uv run python scripts/import_master_data.py

Deterministic UUIDs (uuid5) — safe to re-run; existing rows are skipped.
"""

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone

import psycopg

# ── Database ───────────────────────────────────────────────────────────────────
DB = dict(dbname="mini_erp", user="postgres", password="postgres",
          host="localhost", port=5433)

NS = uuid.NAMESPACE_OID
NOW = datetime.now(timezone.utc)


def uid(seed: str) -> str:
    return str(uuid.uuid5(NS, f"mirako-kids:{seed}"))


# ── Reference IDs ──────────────────────────────────────────────────────────────
COMPANY_ID   = uid("company")
WAREHOUSE_ID = uid("warehouse:gudang-utama")

# ── Categories ─────────────────────────────────────────────────────────────────
CATEGORIES = [
    {"id": uid("cat:SEG"), "code": "SEG", "name": "Set / Setelan",  "shopee_cat_id": 525},
    {"id": uid("cat:DRS"), "code": "DRS", "name": "Dress",          "shopee_cat_id": 516},
    {"id": uid("cat:JNG"), "code": "JNG", "name": "Jeans / Pants",  "shopee_cat_id": 517},
    {"id": uid("cat:PNG"), "code": "PNG", "name": "Pants",          "shopee_cat_id": 515},
]
CAT_BY_CODE = {c["code"]: c["id"] for c in CATEGORIES}

# ── Products (43 SKUs from Master SKU tab) ─────────────────────────────────────
# (sku_code, name, category_code)
_PRODUCTS_RAW = [
    ("SEG-001", "Kyra",    "SEG"),
    ("DRS-002", "Daisy",   "DRS"),
    ("DRS-003", "Claire",  "DRS"),
    ("SEG-004", "Chloe",   "SEG"),
    ("SEG-005", "Scarlet", "SEG"),
    ("SEG-006", "Nancy",   "SEG"),
    ("SEG-007", "Yuki",    "SEG"),
    ("DRS-008", "Lulu",    "DRS"),
    ("SEG-009", "Ella",    "SEG"),
    ("JNG-010", "Roxy",    "JNG"),
    ("JNG-011", "Nicole",  "JNG"),
    ("SEG-012", "Lily",    "SEG"),
    ("SEG-013", "Stella",  "SEG"),
    ("SEG-014", "Emma",    "SEG"),
    ("SEG-015", "June",    "SEG"),
    ("SEG-016", "Mila",    "SEG"),
    ("SEG-017", "Emily",   "SEG"),
    ("SEG-018", "Sophie",  "SEG"),
    ("SEG-019", "Ivy",     "SEG"),
    ("SEG-020", "Isla",    "SEG"),
    ("SEG-021", "Aria",    "SEG"),
    ("SEG-022", "Nina",    "SEG"),
    ("SEG-023", "Zoe",     "SEG"),
    ("SEG-024", "Clara",   "SEG"),
    ("SEG-025", "Aurel",   "SEG"),
    ("DRS-026", "Lana",    "DRS"),
    ("SEG-027", "Rosie",   "SEG"),
    ("SEG-028", "Annie",   "SEG"),
    ("SEG-029", "Bella",   "SEG"),
    ("SEG-030", "Talia",   "SEG"),
    ("SEG-031", "Molly",   "SEG"),
    ("SEG-032", "Celia",   "SEG"),
    ("SEG-033", "Lila",    "SEG"),
    ("SEG-034", "Jasmine", "SEG"),
    ("SEG-035", "Jessie",  "SEG"),
    ("SEG-036", "Eliza",   "SEG"),
    ("SEG-037", "Maria",   "SEG"),
    ("DRS-038", "Lisa",    "DRS"),
    ("SEG-039", "Giselle", "SEG"),
    ("SEG-040", "Amelie",  "SEG"),
    ("SEG-041", "Tina",    "SEG"),
    ("SEG-042", "Maura",   "SEG"),
    ("SEG-043", "Bonnie",  "SEG"),
]
PROD_NAME = {sku: name for sku, name, _ in _PRODUCTS_RAW}
PROD_CAT   = {sku: cat  for sku, _, cat  in _PRODUCTS_RAW}

# ── Variants (140 rows from Master SKU Variant + Summary Inventory tabs) ────────
# (sku_variant_code, product_sku, color, size, cogs_idr, base_price_idr, physical_qty)
VARIANTS = [
    # ── SEG-001 Kyra — White ──────────────────────────────────────────────────
    ("SEG-001-100-WHT", "SEG-001", "White",      "100", 93000,  185000, 48),
    ("SEG-001-110-WHT", "SEG-001", "White",      "110", 93000,  185000, 39),
    ("SEG-001-120-WHT", "SEG-001", "White",      "120", 93000,  185000, 33),
    ("SEG-001-130-WHT", "SEG-001", "White",      "130", 93000,  185000, 31),
    ("SEG-001-140-WHT", "SEG-001", "White",      "140", 93000,  185000, 28),
    # ── DRS-002 Daisy — White ─────────────────────────────────────────────────
    ("DRS-002-100-WHT", "DRS-002", "White",      "100", 86000,  179000, 15),
    ("DRS-002-110-WHT", "DRS-002", "White",      "110", 86000,  179000, 15),
    ("DRS-002-120-WHT", "DRS-002", "White",      "120", 86000,  179000, 15),
    ("DRS-002-130-WHT", "DRS-002", "White",      "130", 86000,  179000, 15),
    ("DRS-002-140-WHT", "DRS-002", "White",      "140", 86000,  179000, 15),
    # ── DRS-003 Claire — Peach ───────────────────────────────────────────────
    ("DRS-003-100-PEA", "DRS-003", "Peach",      "100", 81000,  169000, 0),
    ("DRS-003-110-PEA", "DRS-003", "Peach",      "110", 81000,  169000, 0),
    ("DRS-003-120-PEA", "DRS-003", "Peach",      "120", 81000,  169000, 0),
    ("DRS-003-130-PEA", "DRS-003", "Peach",      "130", 81000,  169000, 0),
    ("DRS-003-140-PEA", "DRS-003", "Peach",      "140", 81000,  169000, 0),
    # ── SEG-004 Chloe — Pink ─────────────────────────────────────────────────
    ("SEG-004-100-PNK", "SEG-004", "Pink",       "100", 99000,  189000, 0),
    ("SEG-004-110-PNK", "SEG-004", "Pink",       "110", 99000,  189000, 0),
    ("SEG-004-120-PNK", "SEG-004", "Pink",       "120", 99000,  189000, 0),
    ("SEG-004-130-PNK", "SEG-004", "Pink",       "130", 99000,  189000, 0),
    ("SEG-004-140-PNK", "SEG-004", "Pink",       "140", 99000,  189000, 0),
    # ── SEG-005 Scarlet — Red ────────────────────────────────────────────────
    ("SEG-005-100-RED", "SEG-005", "Red",        "100", 95000,  199000, 57),
    ("SEG-005-110-RED", "SEG-005", "Red",        "110", 95000,  199000, 111),
    ("SEG-005-120-RED", "SEG-005", "Red",        "120", 95000,  199000, 52),
    ("SEG-005-130-RED", "SEG-005", "Red",        "130", 95000,  199000, 44),
    ("SEG-005-140-RED", "SEG-005", "Red",        "140", 95000,  199000, 72),
    # ── SEG-006 Nancy — White ────────────────────────────────────────────────
    ("SEG-006-100-WHT", "SEG-006", "White",      "100", 79000,  169000, 37),
    ("SEG-006-110-WHT", "SEG-006", "White",      "110", 79000,  169000, 24),
    ("SEG-006-120-WHT", "SEG-006", "White",      "120", 79000,  169000, 31),
    ("SEG-006-130-WHT", "SEG-006", "White",      "130", 79000,  169000, 28),
    ("SEG-006-140-WHT", "SEG-006", "White",      "140", 79000,  169000, 40),
    # ── SEG-007 Yuki — White ─────────────────────────────────────────────────
    ("SEG-007-100-WHT", "SEG-007", "White",      "100", 84000,  169000, 32),
    ("SEG-007-110-WHT", "SEG-007", "White",      "110", 84000,  169000, 30),
    ("SEG-007-120-WHT", "SEG-007", "White",      "120", 84000,  169000, 22),
    ("SEG-007-130-WHT", "SEG-007", "White",      "130", 84000,  169000, 10),
    ("SEG-007-140-WHT", "SEG-007", "White",      "140", 84000,  169000, 16),
    # ── DRS-008 Lulu — Blue ──────────────────────────────────────────────────
    ("DRS-008-100-BLU", "DRS-008", "Blue",       "100", 104000, 199000, 63),
    ("DRS-008-110-BLU", "DRS-008", "Blue",       "110", 104000, 199000, 60),
    ("DRS-008-120-BLU", "DRS-008", "Blue",       "120", 104000, 199000, 56),
    ("DRS-008-130-BLU", "DRS-008", "Blue",       "130", 104000, 199000, 41),
    ("DRS-008-140-BLU", "DRS-008", "Blue",       "140", 104000, 199000, 34),
    # ── SEG-009 Ella — Blue ──────────────────────────────────────────────────
    ("SEG-009-100-BLU", "SEG-009", "Blue",       "100", 101000, 189000, 0),
    ("SEG-009-110-BLU", "SEG-009", "Blue",       "110", 101000, 189000, 0),
    ("SEG-009-120-BLU", "SEG-009", "Blue",       "120", 101000, 189000, 0),
    ("SEG-009-130-BLU", "SEG-009", "Blue",       "130", 101000, 189000, 0),
    ("SEG-009-140-BLU", "SEG-009", "Blue",       "140", 101000, 189000, 0),
    # ── JNG-010 Roxy — Blue ──────────────────────────────────────────────────
    ("JNG-010-100-BLU", "JNG-010", "Blue",       "100", 70000,  139000, 0),
    ("JNG-010-110-BLU", "JNG-010", "Blue",       "110", 70000,  139000, 0),
    ("JNG-010-120-BLU", "JNG-010", "Blue",       "120", 70000,  139000, 0),
    ("JNG-010-130-BLU", "JNG-010", "Blue",       "130", 70000,  139000, 0),
    ("JNG-010-140-BLU", "JNG-010", "Blue",       "140", 70000,  139000, 0),
    # ── SEG-014 Emma — Beige ─────────────────────────────────────────────────
    ("SEG-014-100-BEG", "SEG-014", "Beige",      "100", 76000,  169000, 37),
    ("SEG-014-110-BEG", "SEG-014", "Beige",      "110", 76000,  169000, 33),
    ("SEG-014-120-BEG", "SEG-014", "Beige",      "120", 76000,  169000, 47),
    ("SEG-014-130-BEG", "SEG-014", "Beige",      "130", 76000,  169000, 29),
    ("SEG-014-140-BEG", "SEG-014", "Beige",      "140", 76000,  169000, 28),
    # ── SEG-017 Emily — Light Blue ───────────────────────────────────────────
    ("SEG-017-100-LBU", "SEG-017", "Light Blue", "100", 95000,  139000, 0),
    ("SEG-017-110-LBU", "SEG-017", "Light Blue", "110", 95000,  139000, 0),
    ("SEG-017-120-LBU", "SEG-017", "Light Blue", "120", 95000,  139000, 4),
    ("SEG-017-130-LBU", "SEG-017", "Light Blue", "130", 95000,  139000, 0),
    ("SEG-017-140-LBU", "SEG-017", "Light Blue", "140", 95000,  139000, 0),
    # ── SEG-019 Ivy — Blue + Pink (10 variants) ───────────────────────────────
    ("SEG-019-100-BLU", "SEG-019", "Blue",       "100", 86000,  125000, 7),
    ("SEG-019-100-PNK", "SEG-019", "Pink",       "100", 86000,  125000, 6),
    ("SEG-019-110-BLU", "SEG-019", "Blue",       "110", 86000,  125000, 7),
    ("SEG-019-110-PNK", "SEG-019", "Pink",       "110", 86000,  125000, 5),
    ("SEG-019-120-BLU", "SEG-019", "Blue",       "120", 86000,  125000, 8),
    ("SEG-019-120-PNK", "SEG-019", "Pink",       "120", 86000,  125000, 6),
    ("SEG-019-130-BLU", "SEG-019", "Blue",       "130", 86000,  125000, 10),
    ("SEG-019-130-PNK", "SEG-019", "Pink",       "130", 86000,  125000, 5),
    ("SEG-019-140-BLU", "SEG-019", "Blue",       "140", 86000,  125000, 6),
    ("SEG-019-140-PNK", "SEG-019", "Pink",       "140", 86000,  125000, 5),
    # ── SEG-020 Isla — Blue ───────────────────────────────────────────────────
    ("SEG-020-100-BLU", "SEG-020", "Blue",       "100", 87000,  119000, 23),
    ("SEG-020-110-BLU", "SEG-020", "Blue",       "110", 87000,  119000, 25),
    ("SEG-020-120-BLU", "SEG-020", "Blue",       "120", 87000,  119000, 19),
    ("SEG-020-130-BLU", "SEG-020", "Blue",       "130", 87000,  119000, 25),
    ("SEG-020-140-BLU", "SEG-020", "Blue",       "140", 87000,  119000, 26),
    # ── SEG-021 Aria — Light Blue ─────────────────────────────────────────────
    ("SEG-021-100-LBU", "SEG-021", "Light Blue", "100", 92000,  135000, 0),
    ("SEG-021-110-LBU", "SEG-021", "Light Blue", "110", 92000,  135000, 0),
    ("SEG-021-120-LBU", "SEG-021", "Light Blue", "120", 92000,  135000, 0),
    ("SEG-021-130-LBU", "SEG-021", "Light Blue", "130", 92000,  135000, 0),
    ("SEG-021-140-LBU", "SEG-021", "Light Blue", "140", 92000,  135000, 0),
    # ── SEG-023 Zoe — White ───────────────────────────────────────────────────
    ("SEG-023-100-WHT", "SEG-023", "White",      "100", 86000,  172000, 30),
    ("SEG-023-110-WHT", "SEG-023", "White",      "110", 86000,  172000, 29),
    ("SEG-023-120-WHT", "SEG-023", "White",      "120", 86000,  172000, 23),
    ("SEG-023-130-WHT", "SEG-023", "White",      "130", 86000,  172000, 25),
    ("SEG-023-140-WHT", "SEG-023", "White",      "140", 86000,  172000, 25),
    # ── SEG-024 Clara — White ────────────────────────────────────────────────
    ("SEG-024-100-WHT", "SEG-024", "White",      "100", 83000,  119000, 0),
    ("SEG-024-110-WHT", "SEG-024", "White",      "110", 83000,  119000, 1),
    ("SEG-024-120-WHT", "SEG-024", "White",      "120", 83000,  119000, 4),
    ("SEG-024-130-WHT", "SEG-024", "White",      "130", 83000,  119000, 3),
    ("SEG-024-140-WHT", "SEG-024", "White",      "140", 83000,  119000, 0),
    # ── SEG-025 Aurel — Gray + Black (10 variants) ────────────────────────────
    ("SEG-025-100-GRY", "SEG-025", "Gray",       "100", 91000,  129000, 28),
    ("SEG-025-110-GRY", "SEG-025", "Gray",       "110", 91000,  129000, 17),
    ("SEG-025-120-GRY", "SEG-025", "Gray",       "120", 91000,  129000, 0),
    ("SEG-025-130-GRY", "SEG-025", "Gray",       "130", 91000,  129000, 0),
    ("SEG-025-140-GRY", "SEG-025", "Gray",       "140", 91000,  129000, 0),
    ("SEG-025-100-BLK", "SEG-025", "Black",      "100", 91000,  129000, 10),
    ("SEG-025-110-BLK", "SEG-025", "Black",      "110", 91000,  129000, 6),
    ("SEG-025-120-BLK", "SEG-025", "Black",      "120", 91000,  129000, 7),
    ("SEG-025-130-BLK", "SEG-025", "Black",      "130", 91000,  129000, 8),
    ("SEG-025-140-BLK", "SEG-025", "Black",      "140", 91000,  129000, 10),
    # ── SEG-028 Annie — White ────────────────────────────────────────────────
    ("SEG-028-100-WHT", "SEG-028", "White",      "100", 94000,  135000, 0),
    ("SEG-028-110-WHT", "SEG-028", "White",      "110", 94000,  135000, 0),
    ("SEG-028-120-WHT", "SEG-028", "White",      "120", 94000,  135000, 3),
    ("SEG-028-130-WHT", "SEG-028", "White",      "130", 94000,  135000, 3),
    ("SEG-028-140-WHT", "SEG-028", "White",      "140", 94000,  135000, 0),
    # ── SEG-029 Bella — Beige ────────────────────────────────────────────────
    ("SEG-029-100-BEG", "SEG-029", "Beige",      "100", 83000,  119000, 0),
    ("SEG-029-110-BEG", "SEG-029", "Beige",      "110", 83000,  119000, 0),
    ("SEG-029-120-BEG", "SEG-029", "Beige",      "120", 83000,  119000, 0),
    ("SEG-029-130-BEG", "SEG-029", "Beige",      "130", 83000,  119000, 0),
    ("SEG-029-140-BEG", "SEG-029", "Beige",      "140", 83000,  119000, 0),
    # ── SEG-032 Celia — Pink ─────────────────────────────────────────────────
    ("SEG-032-100-PNK", "SEG-032", "Pink",       "100", 77000,  109000, 0),
    ("SEG-032-110-PNK", "SEG-032", "Pink",       "110", 77000,  109000, 0),
    ("SEG-032-120-PNK", "SEG-032", "Pink",       "120", 77000,  109000, 5),
    ("SEG-032-130-PNK", "SEG-032", "Pink",       "130", 77000,  109000, 9),
    ("SEG-032-140-PNK", "SEG-032", "Pink",       "140", 77000,  109000, 5),
    # ── SEG-036 Eliza — White ────────────────────────────────────────────────
    ("SEG-036-100-WHT", "SEG-036", "White",      "100", 94000,  135000, 0),
    ("SEG-036-110-WHT", "SEG-036", "White",      "110", 94000,  135000, 0),
    ("SEG-036-120-WHT", "SEG-036", "White",      "120", 94000,  135000, 0),
    ("SEG-036-130-WHT", "SEG-036", "White",      "130", 94000,  135000, 0),
    ("SEG-036-140-WHT", "SEG-036", "White",      "140", 94000,  135000, 0),
    # ── SEG-039 Giselle — Blue ───────────────────────────────────────────────
    ("SEG-039-100-BLU", "SEG-039", "Blue",       "100", 95000,  129000, 102),
    ("SEG-039-110-BLU", "SEG-039", "Blue",       "110", 95000,  129000, 126),
    ("SEG-039-120-BLU", "SEG-039", "Blue",       "120", 95000,  129000, 91),
    ("SEG-039-130-BLU", "SEG-039", "Blue",       "130", 95000,  129000, 127),
    ("SEG-039-140-BLU", "SEG-039", "Blue",       "140", 95000,  129000, 194),
    # ── SEG-040 Amelie — White + Pink (10 variants) ───────────────────────────
    ("SEG-040-100-WHT", "SEG-040", "White",      "100", 88000,  129000, 2),
    ("SEG-040-110-WHT", "SEG-040", "White",      "110", 88000,  129000, 1),
    ("SEG-040-120-WHT", "SEG-040", "White",      "120", 88000,  129000, 5),
    ("SEG-040-130-WHT", "SEG-040", "White",      "130", 88000,  129000, 2),
    ("SEG-040-140-WHT", "SEG-040", "White",      "140", 88000,  129000, 0),
    ("SEG-040-100-PNK", "SEG-040", "Pink",       "100", 88000,  129000, 0),
    ("SEG-040-110-PNK", "SEG-040", "Pink",       "110", 88000,  129000, 0),
    ("SEG-040-120-PNK", "SEG-040", "Pink",       "120", 88000,  129000, 0),
    ("SEG-040-130-PNK", "SEG-040", "Pink",       "130", 88000,  129000, 0),
    ("SEG-040-140-PNK", "SEG-040", "Pink",       "140", 88000,  129000, 0),
    # ── SEG-043 Bonnie — Blue ────────────────────────────────────────────────
    ("SEG-043-100-BLU", "SEG-043", "Blue",       "100", 94000,  135000, 0),
    ("SEG-043-110-BLU", "SEG-043", "Blue",       "110", 94000,  135000, 0),
    ("SEG-043-120-BLU", "SEG-043", "Blue",       "120", 94000,  135000, 0),
    ("SEG-043-130-BLU", "SEG-043", "Blue",       "130", 94000,  135000, 0),
    ("SEG-043-140-BLU", "SEG-043", "Blue",       "140", 94000,  135000, 0),
]

# ── Derived lookups ────────────────────────────────────────────────────────────

def _build_product_aggregates():
    """Compute total_qty, total_cogs, and variant_options per product."""
    total_qty  = defaultdict(int)
    total_cogs = defaultdict(int)
    colors     = defaultdict(set)
    sizes      = defaultdict(set)

    for code, prod_sku, color, size, cogs, _price, stock in VARIANTS:
        total_qty[prod_sku]  += stock
        total_cogs[prod_sku] += cogs * stock
        colors[prod_sku].add(color)
        sizes[prod_sku].add(size)

    variant_options = {}
    for sku in colors:
        variant_options[sku] = {
            "color": sorted(colors[sku]),
            "size":  sorted(sizes[sku], key=lambda s: int(s) if s.isdigit() else s),
        }
    return total_qty, total_cogs, variant_options


def main():
    total_qty, total_cogs, variant_options = _build_product_aggregates()

    conn = psycopg.connect(**DB)
    cur  = conn.cursor()

    try:
        # Disable SKU-generation triggers so we can provide our own codes.
        cur.execute("ALTER TABLE inventory_product DISABLE TRIGGER trg_generate_sku")
        cur.execute("ALTER TABLE inventory_productvariant DISABLE TRIGGER trg_generate_variant_sku")

        print("── Company ──────────────────────────────────────────────────────")
        cur.execute("""
            INSERT INTO core_company
              (company_id, name, address, is_active, cdate, udate)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (company_id) DO NOTHING
        """, (COMPANY_ID, "Mirako Kids", "Indonesia", True, NOW, NOW))
        print(f"  company_id = {COMPANY_ID}")

        print("── Warehouse ────────────────────────────────────────────────────")
        cur.execute("""
            INSERT INTO inventory_warehouse
              (warehouse_id, company_id, name, address,
               is_active, is_marketplace_visible, cdate, udate)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (warehouse_id) DO NOTHING
        """, (WAREHOUSE_ID, COMPANY_ID, "Gudang Utama Mirako Kids",
              "Jakarta, Indonesia", True, True, NOW, NOW))
        print(f"  warehouse_id = {WAREHOUSE_ID}")

        print("── Categories ───────────────────────────────────────────────────")
        # Fetch existing categories to reuse their IDs for FK consistency.
        cur.execute("SELECT category_id::text, category_code FROM inventory_category")
        existing_cats = {row[1]: row[0] for row in cur.fetchall()}

        cat_id_by_code: dict[str, str] = {}
        for cat in CATEGORIES:
            if cat["code"] in existing_cats:
                cat_id_by_code[cat["code"]] = existing_cats[cat["code"]]
                print(f"  reuse {cat['code']} → {existing_cats[cat['code']]}")
            else:
                cur.execute("""
                    INSERT INTO inventory_category
                      (category_id, company_id, name, category_code,
                       master_category_key, shopee_category_id, is_active, cdate, udate)
                    VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (category_id) DO NOTHING
                """, (cat["id"], COMPANY_ID, cat["name"], cat["code"],
                      "", cat["shopee_cat_id"], True, NOW, NOW))
                cat_id_by_code[cat["code"]] = cat["id"]
                print(f"  insert {cat['code']} → {cat['id']}")
        print(f"  {len(cat_id_by_code)} categories ready")

        print("── Products ─────────────────────────────────────────────────────")
        n_prod = 0
        for sku_code, prod_name, cat_code in _PRODUCTS_RAW:
            prod_id    = uid(f"product:{sku_code}")
            cat_id     = cat_id_by_code[cat_code]
            qty        = total_qty.get(sku_code, 0)
            cogs_total = total_cogs.get(sku_code, 0)
            vopts      = json.dumps(variant_options.get(sku_code, {}))
            specs      = json.dumps({})
            ship_cfg   = json.dumps({})
            cur.execute("""
                INSERT INTO inventory_product
                  (product_id, company_id, category_id, name, sku_code,
                   total_qty, total_cogs, variant_options, specifications, shipping_config,
                   weight, length, width, height, is_active, cdate, udate)
                VALUES
                  (%s::uuid, %s::uuid, %s::uuid, %s, %s,
                   %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                   %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (product_id) DO NOTHING
            """, (prod_id, COMPANY_ID, cat_id, prod_name, sku_code,
                  qty, cogs_total, vopts, specs, ship_cfg,
                  0, 0, 0, 0, True, NOW, NOW))
            n_prod += 1
        print(f"  inserted {n_prod} products")

        print("── Variants ─────────────────────────────────────────────────────")
        n_var = 0
        for code, prod_sku, color, size, cogs, price, stock in VARIANTS:
            var_id  = uid(f"variant:{code}")
            prod_id = uid(f"product:{prod_sku}")
            vname   = f"{PROD_NAME[prod_sku]} - {color} {size}"
            vvalues = json.dumps({"color": color, "size": size})
            cur.execute("""
                INSERT INTO inventory_productvariant
                  (product_variant_id, company_id, product_id, name, sku_variant_code,
                   variant_values, current_cogs, base_price,
                   total_incoming_qty, total_outgoing_qty, total_available_qty,
                   is_active, is_fake, cdate, udate)
                VALUES
                  (%s::uuid, %s::uuid, %s::uuid, %s, %s,
                   %s::jsonb, %s, %s,
                   %s, %s, %s,
                   %s, %s, %s, %s)
                ON CONFLICT (product_variant_id) DO NOTHING
            """, (var_id, COMPANY_ID, prod_id, vname, code,
                  vvalues, cogs, price,
                  stock, 0, stock,
                  True, False, NOW, NOW))
            n_var += 1
        print(f"  inserted {n_var} variants")

        print("── Variant-Warehouse stock ───────────────────────────────────────")
        n_vwh = 0
        for code, _prod_sku, _color, _size, _cogs, _price, stock in VARIANTS:
            vwh_id = uid(f"varwh:{code}")
            var_id = uid(f"variant:{code}")
            cur.execute("""
                INSERT INTO inventory_productvariantwarehouse
                  (product_variant_warehouse_id, company_id, product_variant_id, warehouse_id,
                   incoming_qty, outgoing_qty, physical_qty, checkout_qty,
                   cdate, udate)
                VALUES
                  (%s::uuid, %s::uuid, %s::uuid, %s::uuid,
                   %s, %s, %s, %s,
                   %s, %s)
                ON CONFLICT (product_variant_id, warehouse_id) DO NOTHING
            """, (vwh_id, COMPANY_ID, var_id, WAREHOUSE_ID,
                  stock, 0, stock, 0,
                  NOW, NOW))
            n_vwh += 1
        print(f"  inserted {n_vwh} variant-warehouse rows")

        conn.commit()

        # Re-enable triggers after commit (cannot ALTER inside a transaction with pending events).
        cur.execute("ALTER TABLE inventory_product ENABLE TRIGGER trg_generate_sku")
        cur.execute("ALTER TABLE inventory_productvariant ENABLE TRIGGER trg_generate_variant_sku")
        conn.commit()

        print()
        print("✓ Import complete.")
        print(f"  Company    : Mirako Kids  ({COMPANY_ID})")
        print(f"  Warehouse  : Gudang Utama ({WAREHOUSE_ID})")
        print(f"  Categories : {len(cat_id_by_code)}")
        print(f"  Products   : {n_prod}")
        print(f"  Variants   : {n_var}")

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
