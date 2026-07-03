"""
Mirako Data Migration — Master Script

Migrates a blank database to fully seeded state in one command.
Source data: mirako_data/ folder (sibling of mini-erp-backend/).

Usage:
    uv run python scripts/migrate_all_mirako_kids.py
    uv run python scripts/migrate_all_mirako_kids.py --skip-files   # skip R2 uploads

Steps:
    1. Reset      — truncate all business tables
    2. Seed       — create company Mirako + superuser admin/admin123
    3. Master     — products, variants, categories, suppliers
    4. PO         — purchase orders + line items + FIFO
    5. PO enrich  — invoice numbers, delivery orders, fees from PO Tracker
    6. Sales      — sales orders from Sales Order Tracker
    7. Cash       — cash transactions from FinOps Finance sheet
    8. Files      — upload PO invoices, resi, DO invoices to R2
    9. Report     — print reconciliation counts
"""

import argparse
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap Django before any Django imports
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
import django

django.setup()

import openpyxl
import psycopg
from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.files.storage import default_storage

from core.models import Company, UserProfile

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MIRAKO_DATA = Path(__file__).parent.parent.parent / "mirako_data"
OPEX_DOCS = MIRAKO_DATA / "opex docs"

DB_PARAMS = dict(
    host="localhost",
    port=5433,
    dbname="mini_erp",
    user="postgres",
    password="postgres",
)

SCRIPTS_DIR = Path(__file__).parent

IMPORTED_PO_NUMBERS = [f"PO-2025-{i:03d}" for i in range(1, 8)] + [
    f"PO-2026-{i:03d}" for i in range(1, 8)
]

# tracker "no" (1-14) -> PO number
TRACKER_NO_TO_PO = {
    1: "PO-2025-001",
    2: "PO-2025-002",
    3: "PO-2025-003",
    4: "PO-2025-004",
    5: "PO-2025-005",
    6: "PO-2025-006",
    7: "PO-2025-007",
    8: "PO-2026-001",
    9: "PO-2026-002",
    10: "PO-2026-003",
    11: "PO-2026-004",
    12: "PO-2026-005",
    13: "PO-2026-006",
    14: "PO-2026-007",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def run_script(name: str, script: str) -> None:
    """Run a migration script as subprocess; exit on failure."""
    header(f"Step: {name}")
    result = subprocess.run(
        ["uv", "run", "python", str(SCRIPTS_DIR / script)],
        cwd=str(SCRIPTS_DIR.parent),
    )
    if result.returncode != 0:
        print(f"\n[FAIL] {name} exited with code {result.returncode}")
        sys.exit(1)
    print(f"\n[OK] {name}")


def _clean_resi_filename(raw: str) -> str:
    """'RESI-GZ3-41906.pdf (WZ)' -> 'RESI-GZ3-41906.pdf'"""
    return re.sub(r"\s*\([^)]*\)\s*$", "", raw.strip())


def _clean_doi_filename(raw: str) -> str:
    """'INVOICE/BRG/81524.pdf' -> 'INVOICE_BRG_81524.pdf'"""
    return raw.replace("/", "_")


def _to_date(v) -> date | None:
    if v is None:
        return None
    if hasattr(v, "date"):
        return v.date()
    if isinstance(v, date):
        return v
    return None


# ---------------------------------------------------------------------------
# Step 1 — Reset
# ---------------------------------------------------------------------------
def step_reset() -> None:
    header("Step 1: Reset business tables")
    conn = psycopg.connect(**DB_PARAMS, autocommit=True)
    cur = conn.cursor()
    cur.execute("""
        TRUNCATE TABLE
            sales_salesreturnitem, sales_salesreturn,
            sales_salesordercogsdetail, sales_salesorderitem, sales_salesorder,
            inventory_productcogs,
            finance_paymentrecord, finance_accountspayable,
            finance_accountsreceivable, finance_cashtransaction,
            purchasing_purchaseorderstatushistory,
            purchasing_purchaseorderdetail, purchasing_purchaseorder,
            po_number_counter,
            inventory_stockmovement,
            inventory_productvariantwarehouse, inventory_productvariantmarketplace,
            inventory_productbusinessentity, inventory_productsupplier,
            inventory_productdimensionimage, product_photo,
            purchasing_sourcingpoolitem, purchasing_sourcingpool,
            inventory_productvariant, inventory_product,
            inventory_supplier, inventory_category
        RESTART IDENTITY CASCADE
    """)
    conn.close()
    print("[OK] All business tables truncated")


# ---------------------------------------------------------------------------
# Step 2 — Seed user & company
# ---------------------------------------------------------------------------
def step_seed_user() -> None:
    header("Step 2: Seed user & company")
    User = get_user_model()

    company, created = Company.objects.get_or_create(
        name="Mirako",
        defaults={"is_active": True},
    )
    print(f"{'Created' if created else 'Existing'} company: {company.name} (id={company.id})")

    USERNAME = "admin"
    PASSWORD = "admin123"

    if not User.objects.filter(username=USERNAME).exists():
        user = User.objects.create_superuser(
            username=USERNAME,
            email="albert.christiann18@gmail.com",
            password=PASSWORD,
        )
        user_created = True
    else:
        user = User.objects.get(username=USERNAME)
        user_created = False

    print(f"{'Created' if user_created else 'Existing'} user: {USERNAME} / {PASSWORD}")

    profile, profile_created = UserProfile.objects.get_or_create(
        user=user,
        defaults={"company": company, "role": "admin"},
    )
    if not profile_created and profile.company != company:
        profile.company = company
        profile.save(update_fields=["company"])

    print(f"Profile role={profile.role}, company={profile.company.name}")
    print(f"\n  Login: {USERNAME} / {PASSWORD}")


# ---------------------------------------------------------------------------
# Step 3 — Master data (products, variants, categories, suppliers)
# ---------------------------------------------------------------------------
def step_master_data() -> None:
    run_script("Master Data", "import_master_data_excel.py")


# ---------------------------------------------------------------------------
# Step 4 — Purchase orders
# ---------------------------------------------------------------------------
def step_purchase_orders() -> None:
    run_script("Purchase Orders", "import_purchase_orders_excel.py")


# ---------------------------------------------------------------------------
# Step 5 — PO tracker enrichment
# ---------------------------------------------------------------------------
def parse_po_tracker() -> dict:
    """
    Read Purchase Order Tracker (1).xlsx and return per-PO metadata dict.
    Keys are PO numbers (e.g. "PO-2025-001").
    """
    wb = openpyxl.load_workbook(MIRAKO_DATA / "Purchase Order Tracker (1).xlsx", data_only=True)
    ws = wb["Master Tracker Purchase Order"]
    rows = list(ws.iter_rows(values_only=True))
    # Header at row index 6, data from row index 7
    by_no: dict[int, list] = defaultdict(list)
    for row in rows[7:]:
        no = row[0]
        if not isinstance(no, (int, float)):
            continue
        no_int = int(no)
        if no_int not in TRACKER_NO_TO_PO:
            continue
        by_no[no_int].append(row)

    result = {}
    for no, po_rows in by_no.items():
        first = po_rows[0]
        po_number = TRACKER_NO_TO_PO[no]

        # DO NUMBER cleaning (col 4)
        raw_do = str(first[4]) if first[4] else ""
        resi_filename = _clean_resi_filename(raw_do) if raw_do else ""
        resi_number = re.sub(r"\.pdf.*$", "", resi_filename, flags=re.IGNORECASE).strip()

        # DO invoice filename (col 19)
        raw_doi = str(first[19]) if first[19] else ""
        doi_filename = _clean_doi_filename(raw_doi) if raw_doi else ""

        # PO invoice file (col 12) — only attach if it's a PDF
        po_inv_raw = str(first[12]) if first[12] else ""
        po_invoice_file = po_inv_raw if po_inv_raw.lower().endswith(".pdf") else None

        # Aggregate per-delivery values (sum across all delivery rows for this PO)
        # delivery_fee: take first row only (IDR), kept as IDR for later RMB conversion
        delivery_fee_idr_first_row = float(po_rows[0][15] or 0)
        shipping_fee = int(sum((row[16] or 0) for row in po_rows))
        cbm = round(sum((row[20] or 0) for row in po_rows), 3)
        weight = round(sum((row[23] or 0) for row in po_rows), 3)

        result[po_number] = {
            "invoice_number": str(first[2]) if first[2] else None,
            "invoice_date": _to_date(first[3]),
            "delivery_order_number": resi_number or None,
            "delivery_date": _to_date(first[5]),
            "forwarder_name": str(first[9]) if first[9] else None,
            "shop_services": str(first[10]) if first[10] else None,
            "commission_fee_pct": int(round((first[11] or 0) * 100)),
            "delivery_fee_idr_first_row": delivery_fee_idr_first_row,
            "shipping_fee": shipping_fee,
            "cbm": cbm,
            "weight": weight,
            # file attachment
            "po_invoice_file": po_invoice_file,
            "resi_file": resi_filename or None,
            "do_invoice_file": doi_filename or None,
        }

    return result


def step_enrich_po_tracker() -> None:
    header("Step 5: PO Tracker enrichment")
    metadata = parse_po_tracker()

    conn = psycopg.connect(**DB_PARAMS, autocommit=True)
    cur = conn.cursor()

    updated = 0
    for po_number, m in metadata.items():
        # Fetch current total_item_amount and exchange_rate to recalculate totals
        cur.execute(
            "SELECT total_item_amount, exchange_rate FROM purchasing_purchaseorder "
            "WHERE purchase_order_number = %s",
            (po_number,),
        )
        row = cur.fetchone()
        if not row:
            print(f"  WARNING: PO {po_number} not found — skipping enrichment")
            continue
        total_item_amount = row[0] or 0
        exchange_rate = float(row[1] or 1)

        # Convert delivery_fee from IDR (first row) to RMB for DB storage
        delivery_fee_rmb = round(m["delivery_fee_idr_first_row"] / exchange_rate, 3)
        delivery_fee_idr_calc = int(round(delivery_fee_rmb * exchange_rate))
        commission_fee = round(m["commission_fee_pct"] / 100 * total_item_amount)
        shipping_fee = m["shipping_fee"]
        total_order_amount = total_item_amount + commission_fee + delivery_fee_idr_calc
        total_amount = total_item_amount + commission_fee + shipping_fee + delivery_fee_idr_calc

        cur.execute(
            """
            UPDATE purchasing_purchaseorder SET
                invoice_number        = %s,
                invoice_date          = %s,
                delivery_order_number = %s,
                delivery_date         = %s,
                forwarder_name        = %s,
                shop_services         = %s,
                commission_fee_pct    = %s,
                delivery_fee          = %s,
                shipping_fee          = %s,
                cbm                   = %s,
                weight                = %s,
                total_order_amount    = %s,
                total_amount          = %s
            WHERE purchase_order_number = %s
            """,
            (
                m["invoice_number"],
                m["invoice_date"],
                m["delivery_order_number"],
                m["delivery_date"],
                m["forwarder_name"],
                m["shop_services"],
                m["commission_fee_pct"],
                delivery_fee_rmb,
                m["shipping_fee"],
                m["cbm"],
                m["weight"],
                total_order_amount,
                total_amount,
                po_number,
            ),
        )
        print(f"  ✓ {po_number}: inv={m['invoice_number']}, DO={m['delivery_order_number']}")
        updated += 1

    conn.close()
    print(f"\n[OK] Enriched {updated} POs from tracker")


# ---------------------------------------------------------------------------
# Step 6 — Sales orders
# ---------------------------------------------------------------------------
def step_sales_orders() -> None:
    run_script("Sales Orders", "import_sales_excel.py")


# ---------------------------------------------------------------------------
# Step 7 — Cash transactions
# ---------------------------------------------------------------------------
def step_cash_transactions() -> None:
    run_script("Cash Transactions", "import_cash_transactions_excel.py")


# ---------------------------------------------------------------------------
# Step 8 — File attachments
# ---------------------------------------------------------------------------
def _upload_file(cur, local_path: Path, storage_key: str, po_number: str, field: str) -> bool:
    """Upload a file to R2 and UPDATE the DB field. Returns True on success."""
    if not local_path.exists():
        print(f"  SKIP  {po_number}.{field}: file not found — {local_path.name}")
        return False
    try:
        if default_storage.exists(storage_key):
            default_storage.delete(storage_key)
        with open(local_path, "rb") as f:
            saved_name = default_storage.save(storage_key, File(f))
        cur.execute(
            f"UPDATE purchasing_purchaseorder SET {field} = %s WHERE purchase_order_number = %s",
            (saved_name, po_number),
        )
        print(f"  ✓ {po_number}.{field} ← {local_path.name}")
        return True
    except Exception as e:
        print(f"  ERROR {po_number}.{field}: {e}")
        return False


def step_attach_files(skip_files: bool) -> None:
    header("Step 8: File attachments")
    if skip_files:
        print("[SKIP] --skip-files flag set")
        return

    metadata = parse_po_tracker()
    conn = psycopg.connect(**DB_PARAMS, autocommit=True)
    cur = conn.cursor()

    # Clear existing file references on imported POs
    cur.execute(
        """
        UPDATE purchasing_purchaseorder
        SET purchase_order_invoice_file = NULL,
            delivery_order_file = NULL,
            delivery_order_invoice_file = NULL
        WHERE purchase_order_number = ANY(%s)
        """,
        (IMPORTED_PO_NUMBERS,),
    )

    po_inv_count = do_count = doi_count = 0

    for po_number, m in metadata.items():
        # 1. PO invoice file → opex docs/purchase order invoice/
        if m["po_invoice_file"]:
            local = OPEX_DOCS / "purchase order invoice" / m["po_invoice_file"]
            key = "po/invoices/" + m["po_invoice_file"].replace(" ", "_")
            if _upload_file(cur, local, key, po_number, "purchase_order_invoice_file"):
                po_inv_count += 1

        # 2. RESI / delivery order file → opex docs/resi/
        if m["resi_file"]:
            local = OPEX_DOCS / "resi" / m["resi_file"]
            key = "po/delivery_orders/" + m["resi_file"].replace(" ", "_")
            if _upload_file(cur, local, key, po_number, "delivery_order_file"):
                do_count += 1

        # 3. DO invoice file → opex docs/invoice delivery file/
        if m["do_invoice_file"]:
            local = OPEX_DOCS / "invoice delivery file" / m["do_invoice_file"]
            key = "po/do_invoices/" + m["do_invoice_file"].replace(" ", "_")
            if _upload_file(cur, local, key, po_number, "delivery_order_invoice_file"):
                doi_count += 1

    conn.close()
    print(f"\n[OK] Attached: {po_inv_count} PO invoices, {do_count} resi, {doi_count} DO invoices")


# ---------------------------------------------------------------------------
# Step 9 — Reconciliation report
# ---------------------------------------------------------------------------
def step_reconciliation() -> None:
    header("Step 9: Reconciliation")
    conn = psycopg.connect(**DB_PARAMS, autocommit=True)
    cur = conn.cursor()

    checks = [
        ("Products", "SELECT COUNT(*) FROM inventory_product"),
        ("Variants", "SELECT COUNT(*) FROM inventory_productvariant"),
        ("Purchase Orders", "SELECT COUNT(*) FROM purchasing_purchaseorder"),
        ("PO Details", "SELECT COUNT(*) FROM purchasing_purchaseorderdetail"),
        ("FIFO records", "SELECT COUNT(*) FROM inventory_productcogs"),
        ("Sales Orders", "SELECT COUNT(*) FROM sales_salesorder"),
        ("Sales Items", "SELECT COUNT(*) FROM sales_salesorderitem"),
        ("Cash Transactions", "SELECT COUNT(*) FROM finance_cashtransaction"),
        (
            "POs with invoice_number",
            "SELECT COUNT(*) FROM purchasing_purchaseorder WHERE invoice_number IS NOT NULL",
        ),
        (
            "POs with delivery_order_number",
            "SELECT COUNT(*) FROM purchasing_purchaseorder WHERE delivery_order_number IS NOT NULL",
        ),
        (
            "POs with PO invoice file",
            "SELECT COUNT(*) FROM purchasing_purchaseorder WHERE purchase_order_invoice_file IS NOT NULL",
        ),
        (
            "POs with resi file",
            "SELECT COUNT(*) FROM purchasing_purchaseorder WHERE delivery_order_file IS NOT NULL",
        ),
        (
            "POs with DO invoice file",
            "SELECT COUNT(*) FROM purchasing_purchaseorder WHERE delivery_order_invoice_file IS NOT NULL",
        ),
        (
            "Negative FIFO qty",
            "SELECT COUNT(*) FROM inventory_productcogs WHERE remaining_qty < 0",
        ),
    ]

    all_ok = True
    for label, sql in checks:
        cur.execute(sql)
        count = cur.fetchone()[0]
        status = "[WARN]" if (label == "Negative FIFO qty" and count > 0) else "[OK]  "
        if "WARN" in status:
            all_ok = False
        print(f"  {status} {label}: {count}")

    conn.close()
    print(f"\n{'All checks passed' if all_ok else 'Some warnings — review above'}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Mirako full data migration")
    parser.add_argument("--skip-files", action="store_true", help="Skip R2 file uploads")
    args = parser.parse_args()

    print("\nMirako Data Migration")
    print(f"MIRAKO_DATA: {MIRAKO_DATA}")
    if not MIRAKO_DATA.exists():
        print(f"ERROR: mirako_data folder not found at {MIRAKO_DATA}")
        sys.exit(1)

    step_reset()
    step_seed_user()
    step_master_data()
    step_purchase_orders()
    step_enrich_po_tracker()
    step_sales_orders()
    step_cash_transactions()
    step_attach_files(args.skip_files)
    step_reconciliation()

    print("\n" + "=" * 60)
    print("  Migration complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
