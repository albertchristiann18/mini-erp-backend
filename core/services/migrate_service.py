"""
Service layer for the migrate_all_mirako management command.

Each step function carries the logic that was previously in scripts/migrate_all_mirako_kids.py.
All DB writes use Django ORM or connection.cursor() — no standalone psycopg.
Decimal is used for all money/quantity fields; never float.
"""

import re
import sys
from collections import defaultdict
from datetime import (
    date,
    datetime,
    timezone as tz,
)
from decimal import Decimal
from typing import TYPE_CHECKING, Any, TypedDict

from django.contrib.auth import get_user_model
from django.db import connection, transaction

if TYPE_CHECKING:
    from core.models import Company


class _SplitConfig(TypedDict):
    parent_po: str
    split_po: str
    sheet: str
    sku_col: int
    date_col: int
    split_date: date
    tracker_no: int


IMPORTED_PO_NUMBERS = [f"PO-2025-{i:03d}" for i in range(1, 8)] + [
    f"PO-2026-{i:03d}" for i in range(1, 8)
]

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

SPLIT_DELIVERY_CONFIG: list[_SplitConfig] = [
    {
        "parent_po": "PO-2025-001",
        "split_po": "PO-2025-001B",
        "sheet": "PO Recap 2 April 2025",
        "sku_col": 8,
        "date_col": 16,
        "split_date": date(2025, 5, 15),
        "tracker_no": 1,
    },
    {
        "parent_po": "PO-2025-002",
        "split_po": "PO-2025-002B",
        "sheet": "PO Recap 2 Juni 2025",
        "sku_col": 9,
        "date_col": 17,
        "split_date": date(2025, 8, 15),
        "tracker_no": 2,
    },
    {
        "parent_po": "PO-2026-001",
        "split_po": "PO-2026-001B",
        "sheet": "Recap 9 Jan 2026",
        "sku_col": 9,
        "date_col": 17,
        "split_date": date(2026, 5, 7),
        "tracker_no": 8,
    },
]


def _clean_resi_filename(raw: str) -> str:
    """'RESI-GZ3-41906.pdf (WZ)' -> 'RESI-GZ3-41906.pdf'"""
    return re.sub(r"\s*\([^)]*\)\s*$", "", raw.strip())


def _clean_doi_filename(raw: str) -> str:
    """'INVOICE/BRG/81524.pdf' -> 'INVOICE_BRG_81524.pdf'"""
    return raw.replace("/", "_")


def _to_date(v: Any) -> date | None:
    if v is None:
        return None
    # datetime is a subclass of date — check it first to extract the date part
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


# ---------------------------------------------------------------------------
# Step 1 — Reset
# ---------------------------------------------------------------------------
def step_reset(stdout: Any) -> None:
    if stdout is None:
        stdout = sys.stdout
    with connection.cursor() as cur:
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
                inventory_productvariantwarehouse, master_productvariantmarketplace,
                inventory_productbusinessentity, inventory_productsupplier,
                inventory_productdimensionimage, master_productphoto,
                purchasing_sourcingpoolitem, purchasing_sourcingpool,
                master_productvariant, master_product,
                inventory_supplier, master_category
            RESTART IDENTITY CASCADE
        """)
    stdout.write("[OK] All business tables truncated\n")


# ---------------------------------------------------------------------------
# Step 2 — Seed user & company
# ---------------------------------------------------------------------------
def step_seed_mirako_user(company_name: str, stdout: Any) -> None:
    if stdout is None:
        stdout = sys.stdout
    from core.services.user_service import create_user

    create_user(
        username="admin", role="admin", password="admin123", company=company_name, stdout=stdout
    )

    User = get_user_model()
    user = User.objects.get(username="admin")
    if not user.is_superuser:
        user.is_superuser = True
        user.save(update_fields=["is_superuser"])
    stdout.write("[OK] admin is_superuser=True\n")


# ---------------------------------------------------------------------------
# Step 3b — Set default product dimensions after import_master_data
# ---------------------------------------------------------------------------
def step_set_product_dimensions(company: "Company", stdout: Any) -> None:
    if stdout is None:
        stdout = sys.stdout
    from apps.catalog.models import Product

    count = Product.objects.filter(company=company).update(
        weight=200, height=1, width=30, length=30
    )
    stdout.write(f"[OK] Set default dimensions on {count} products\n")


# ---------------------------------------------------------------------------
# Step 3c — Product supplier links (1688 URLs from po_detail_wb)
# ---------------------------------------------------------------------------
def step_product_links(company: "Company", po_detail_wb: Any, stdout: Any) -> None:
    if stdout is None:
        stdout = sys.stdout
    from apps.purchasing.models import ProductSupplier

    ws = po_detail_wb["Master DATA All Item"]
    rows = list(ws.iter_rows(values_only=True))

    link_map: dict[str, str] = {}
    for row in rows[2:]:
        sku_code = row[9]
        link = row[1]
        if not sku_code or not link:
            continue
        link_str = str(link).strip()
        if not link_str.startswith("http"):
            continue
        sku_str = str(sku_code).strip()
        parts = sku_str.split("-")
        if len(parts) < 2:
            continue
        product_prefix = f"{parts[0]}-{parts[1]}"
        if product_prefix not in link_map:
            link_map[product_prefix] = link_str

    ps_list = list(ProductSupplier.objects.filter(company=company).select_related("product"))
    to_update: list[ProductSupplier] = []
    for ps in ps_list:
        link = link_map.get(ps.product.sku_code)
        if link:
            ps.supplier_link = link
            to_update.append(ps)
    if to_update:
        ProductSupplier.objects.bulk_update(to_update, ["supplier_link"])
    stdout.write(f"[OK] Updated supplier_link on {len(to_update)} product-supplier records\n")


# ---------------------------------------------------------------------------
# Step 5 — PO tracker enrichment
# ---------------------------------------------------------------------------
def _parse_po_tracker(po_tracker_wb: Any) -> dict[str, dict[str, Any]]:
    """Read the Master Tracker Purchase Order sheet and return per-PO metadata.

    Delegates row grouping to _build_tracker_rows_by_no to avoid duplicated logic.
    """
    by_no = _build_tracker_rows_by_no(po_tracker_wb)

    result: dict[str, dict[str, Any]] = {}
    for no, po_rows in by_no.items():
        first = po_rows[0]
        po_number = TRACKER_NO_TO_PO[no]

        raw_do = str(first[4]) if first[4] else ""
        resi_filename = _clean_resi_filename(raw_do) if raw_do else ""
        resi_number = re.sub(r"\.pdf.*$", "", resi_filename, flags=re.IGNORECASE).strip()

        raw_doi = str(first[19]) if first[19] else ""
        doi_filename = _clean_doi_filename(raw_doi) if raw_doi else ""

        po_inv_raw = str(first[12]) if first[12] else ""
        po_invoice_file = po_inv_raw if po_inv_raw.lower().endswith(".pdf") else None

        # delivery_fee col 30 = shipping_1688 in RMB — Decimal, not float
        delivery_fee_rmb = Decimal(str(first[30] or 0)).quantize(Decimal("0.001"))
        # shipping_fee (IDR) is always an integer; sum with explicit int() to prevent float intermediate
        shipping_fee = sum(int(row[16] or 0) for row in po_rows)
        cbm = round(sum((row[20] or 0) for row in po_rows), 3)
        weight = round(sum((row[23] or 0) for row in po_rows), 3)

        result[po_number] = {
            "invoice_number": str(first[2]) if first[2] else None,
            "invoice_date": _to_date(first[3]),
            "created_date": _to_date(first[1]),
            "delivery_order_number": resi_number or None,
            "delivery_date": _to_date(first[5]),
            "forwarder_name": str(first[9]) if first[9] else None,
            "shop_services": str(first[10]) if first[10] else None,
            "commission_fee_pct": int(round((first[11] or 0) * 100)),
            "delivery_fee_rmb": delivery_fee_rmb,
            "shipping_fee": shipping_fee,
            "cbm": cbm,
            "weight": weight,
            "po_invoice_file": po_invoice_file,
            "resi_file": resi_filename or None,
            "do_invoice_file": doi_filename or None,
        }

    return result


@transaction.atomic
def step_enrich_po_tracker(company: "Company", po_tracker_wb: Any, stdout: Any) -> None:
    if stdout is None:
        stdout = sys.stdout
    from apps.purchasing.models import PurchaseOrder

    metadata = _parse_po_tracker(po_tracker_wb)

    # Fetch all affected POs in one queryset — no ORM calls inside the loop
    po_map = {
        po.purchase_order_number: po
        for po in PurchaseOrder.objects.filter(
            purchase_order_number__in=list(metadata.keys()), company=company
        )
    }

    to_update: list[PurchaseOrder] = []
    missing: list[str] = []

    for po_number, m in metadata.items():
        po = po_map.get(po_number)
        if po is None:
            missing.append(po_number)
            continue

        total_item_amount = po.total_item_amount or 0
        exchange_rate = Decimal(str(po.exchange_rate or 1))
        delivery_fee_rmb: Decimal = m["delivery_fee_rmb"]
        delivery_fee_idr_calc = int((delivery_fee_rmb * exchange_rate).quantize(Decimal("1")))
        commission_fee = int(Decimal(str(m["commission_fee_pct"])) / 100 * total_item_amount)
        shipping_fee: int = m["shipping_fee"]

        po.invoice_number = m["invoice_number"]
        po.invoice_date = m["invoice_date"]
        po.delivery_order_number = m["delivery_order_number"]
        po.delivery_date = m["delivery_date"]
        po.forwarder_name = m["forwarder_name"]
        po.shop_services = m["shop_services"]
        po.commission_fee_pct = m["commission_fee_pct"]
        po.delivery_fee = delivery_fee_rmb
        po.shipping_fee = shipping_fee
        po.cbm = m["cbm"]
        po.weight = m["weight"]
        po.total_order_amount = total_item_amount + commission_fee + delivery_fee_idr_calc
        po.total_amount = total_item_amount + commission_fee + shipping_fee + delivery_fee_idr_calc
        if m["created_date"] is not None:
            po.cdate = datetime.combine(m["created_date"], datetime.min.time()).replace(
                tzinfo=tz.utc
            )

        to_update.append(po)

    for po_number in missing:
        stdout.write(f"  WARNING: PO {po_number} not found — skipping enrichment\n")

    update_fields = [
        "invoice_number",
        "invoice_date",
        "delivery_order_number",
        "delivery_date",
        "forwarder_name",
        "shop_services",
        "commission_fee_pct",
        "delivery_fee",
        "shipping_fee",
        "cbm",
        "weight",
        "total_order_amount",
        "total_amount",
        "cdate",
    ]
    if to_update:
        PurchaseOrder.objects.bulk_update(to_update, update_fields)

    # Set DRAFT status for the two late POs
    PurchaseOrder.objects.filter(
        purchase_order_number__in=["PO-2026-006", "PO-2026-007"], company=company
    ).update(status="DRAFT")

    stdout.write(f"[OK] Enriched {len(to_update)} POs from tracker\n")


# ---------------------------------------------------------------------------
# Step 5b — Split delivery POs
# ---------------------------------------------------------------------------
def _build_tracker_rows_by_no(po_tracker_wb: Any) -> dict[int, list[Any]]:
    ws = po_tracker_wb["Master Tracker Purchase Order"]
    rows = list(ws.iter_rows(values_only=True))
    by_no: dict[int, list[Any]] = defaultdict(list)
    for row in rows[7:]:
        if row is None:
            continue
        no = row[0]
        if isinstance(no, (int, float)) and int(no) in TRACKER_NO_TO_PO:
            by_no[int(no)].append(row)
    return by_no


def step_split_deliveries(
    company: "Company", po_tracker_wb: Any, po_detail_wb: Any, stdout: Any
) -> None:
    if stdout is None:
        stdout = sys.stdout

    # DDL must run outside atomic block (PostgreSQL restriction)
    with connection.cursor() as cur:
        cur.execute("ALTER TABLE purchasing_purchaseorder DISABLE TRIGGER trg_generate_po_number")
    try:
        _persist_split_deliveries(company, po_tracker_wb, po_detail_wb, stdout)
    finally:
        with connection.cursor() as cur:
            cur.execute(
                "ALTER TABLE purchasing_purchaseorder ENABLE TRIGGER trg_generate_po_number"
            )

    stdout.write("[OK] Split delivery POs created\n")


@transaction.atomic
def _persist_split_deliveries(
    company: "Company", po_tracker_wb: Any, po_detail_wb: Any, stdout: Any
) -> None:
    from django.db.models import Sum

    from apps.catalog.models import ProductVariant
    from apps.inventory.models import ProductCogs
    from apps.purchasing.models import PurchaseOrder, PurchaseOrderDetail
    from core.utils import generate_ulid

    by_no = _build_tracker_rows_by_no(po_tracker_wb)

    # SPLIT_DELIVERY_CONFIG has exactly 3 fixed entries — one PO creation per config entry.
    # bulk_create is not applicable here because each split PO creation must be followed by a
    # per-PO PurchaseOrderDetail.update() that references the new PO's pk. The 3-item constant
    # list is not a variable N+1; it is an inherent ordering dependency of the algorithm.
    for cfg in SPLIT_DELIVERY_CONFIG:
        parent_po_num: str = cfg["parent_po"]
        split_po_num: str = cfg["split_po"]
        tracker_no: int = cfg["tracker_no"]
        split_date_val: date = cfg["split_date"]

        try:
            parent_po = PurchaseOrder.objects.select_related("warehouse", "supplier").get(
                purchase_order_number=parent_po_num, company=company
            )
        except PurchaseOrder.DoesNotExist:
            stdout.write(f"  SKIP {split_po_num}: parent {parent_po_num} not found\n")
            continue

        rows_for_no = by_no.get(tracker_no, [])
        if len(rows_for_no) < 2:
            stdout.write(f"  SKIP {split_po_num}: no second tracker row for no={tracker_no}\n")
            continue

        split_row = rows_for_no[1]

        raw_do = str(split_row[4]) if split_row[4] else ""
        resi_filename = _clean_resi_filename(raw_do) if raw_do else ""
        resi_number = re.sub(r"\.pdf.*$", "", resi_filename, flags=re.IGNORECASE).strip()

        # delivery_fee for split row (col 30 = shipping_1688 in RMB) — Decimal, not float
        delivery_fee_rmb = Decimal(str(split_row[30] or 0)).quantize(Decimal("0.001"))
        shipping_fee = int(split_row[16] or 0)
        delivery_date = _to_date(split_row[5])
        created_date = _to_date(split_row[1])

        # Read per-item delivery dates from PO detail sheet to find split variant SKUs
        ws = po_detail_wb[cfg["sheet"]]
        sheet_rows = list(ws.iter_rows(values_only=True))
        split_variant_skus: set[str] = set()
        for row in sheet_rows[2:]:
            sku = row[cfg["sku_col"]]
            item_date = row[cfg["date_col"]]
            if not sku:
                continue
            if _to_date(item_date) == split_date_val:
                split_variant_skus.add(str(sku).strip())

        if not split_variant_skus:
            stdout.write(f"  SKIP {split_po_num}: no items with delivery date {split_date_val}\n")
            continue

        # Resolve variant objects from DB (one queryset, no loop)
        variants = list(
            ProductVariant.objects.filter(sku_variant_code__in=split_variant_skus, company=company)
        )
        found_skus = {v.sku_variant_code for v in variants}
        missing = split_variant_skus - found_skus
        if missing:
            stdout.write(f"    WARNING: {len(missing)} SKUs not in DB: {list(missing)[:5]}\n")
        if not variants:
            stdout.write(f"  SKIP {split_po_num}: no variant IDs resolved\n")
            continue

        stdout.write(
            f"  {split_po_num}: {len(split_variant_skus)} variant SKUs for split date {split_date_val}\n"
        )

        now = datetime.now(tz.utc)
        created_ts = (
            datetime.combine(created_date, datetime.min.time()).replace(tzinfo=tz.utc)
            if created_date
            else now
        )

        split_po = PurchaseOrder.objects.create(
            id=generate_ulid(),
            company=company,
            purchase_order_number=split_po_num,
            status=PurchaseOrder.POStatus.COMPLETED,
            invoice_number=parent_po.invoice_number,
            invoice_date=parent_po.invoice_date,
            delivery_order_number=resi_number or None,
            delivery_date=delivery_date,
            warehouse=parent_po.warehouse,
            supplier=parent_po.supplier,
            supplier_name="Ancorelala",
            currency="CNY",
            exchange_rate=parent_po.exchange_rate,
            commission_fee_pct=parent_po.commission_fee_pct,
            delivery_fee=delivery_fee_rmb,
            shipping_fee=shipping_fee,
            total_ordered_qty=0,
            total_received_qty=0,
            total_item_amount=0,
            procure_amount=0,
            total_order_amount=0,
            total_amount=0,
            has_discount=False,
        )
        # Override auto-set cdate/udate using update (auto_now_add prevents direct set)
        PurchaseOrder.objects.filter(pk=split_po.pk).update(cdate=created_ts)

        # Move matching PO details to the split PO
        PurchaseOrderDetail.objects.filter(
            purchase_order=parent_po, product_variant__in=variants
        ).update(purchase_order=split_po)

        # Update FIFO reference_number for moved variants
        ProductCogs.objects.filter(
            reference_number=parent_po_num, product_variant__in=variants
        ).update(reference_number=split_po_num)

        # Recalculate totals for both POs from their remaining details (no loop, aggregate in SQL).
        # Also recompute total_order_amount and total_amount so they stay consistent with
        # step_enrich_po_tracker's formula after items have been moved between POs.
        for po_obj in (parent_po, split_po):
            agg = PurchaseOrderDetail.objects.filter(purchase_order=po_obj).aggregate(
                total_qty=Sum("ordered_qty"),
                total_item=Sum("total_price_base"),
            )
            qty_total = agg["total_qty"] or 0
            item_amount = agg["total_item"] or 0

            exchange_rate = Decimal(str(po_obj.exchange_rate or 1))
            delivery_fee_rmb = Decimal(str(po_obj.delivery_fee or 0))
            delivery_fee_idr = int((delivery_fee_rmb * exchange_rate).quantize(Decimal("1")))
            commission_fee = int(Decimal(str(po_obj.commission_fee_pct or 0)) / 100 * item_amount)
            shipping_fee_int = int(po_obj.shipping_fee or 0)

            PurchaseOrder.objects.filter(pk=po_obj.pk).update(
                total_ordered_qty=qty_total,
                total_received_qty=qty_total,
                total_item_amount=item_amount,
                procure_amount=item_amount,
                total_order_amount=item_amount + commission_fee + delivery_fee_idr,
                total_amount=item_amount + commission_fee + shipping_fee_int + delivery_fee_idr,
            )

        stdout.write(f"  Created {split_po_num} (RESI={resi_number or 'none'})\n")


# ---------------------------------------------------------------------------
# Step 8 — File attachments
# ---------------------------------------------------------------------------
def _upload_po_file(
    local_path: Any, storage_key: str, po_number: str, field: str, stdout: Any
) -> bool:
    """Upload a file to default_storage and update the PO field via ORM. Returns True on success."""
    from pathlib import Path

    from django.core.files import File
    from django.core.files.storage import default_storage

    from apps.purchasing.models import PurchaseOrder

    path = Path(local_path)
    if not path.exists():
        stdout.write(f"  SKIP  {po_number}.{field}: file not found — {path.name}\n")
        return False
    try:
        if default_storage.exists(storage_key):
            default_storage.delete(storage_key)
        with open(path, "rb") as f:
            saved_name = default_storage.save(storage_key, File(f))
        PurchaseOrder.objects.filter(purchase_order_number=po_number).update(**{field: saved_name})
        stdout.write(f"  [OK] {po_number}.{field} <- {path.name}\n")
        return True
    except Exception as e:
        stdout.write(f"  ERROR {po_number}.{field}: {e}\n")
        return False


def step_attach_files(
    company: "Company",
    po_tracker_wb: Any,
    po_detail_wb: Any,
    skip: bool,
    stdout: Any,
    mirako_data_path: Any = None,
) -> None:
    if stdout is None:
        stdout = sys.stdout
    if skip:
        stdout.write("[SKIP] --skip-files flag set\n")
        return

    from pathlib import Path

    from apps.purchasing.models import PurchaseOrder

    if mirako_data_path is None:
        import pathlib

        mirako_data_path = pathlib.Path(__file__).parent.parent.parent.parent / "mirako_data"
    opex_docs = Path(mirako_data_path) / "opex docs"

    metadata = _parse_po_tracker(po_tracker_wb)

    # Clear existing file references on imported POs via ORM
    PurchaseOrder.objects.filter(
        purchase_order_number__in=IMPORTED_PO_NUMBERS, company=company
    ).update(
        purchase_order_invoice_file=None,
        delivery_order_file=None,
        delivery_order_invoice_file=None,
    )

    split_po_numbers = [c["split_po"] for c in SPLIT_DELIVERY_CONFIG]
    PurchaseOrder.objects.filter(
        purchase_order_number__in=split_po_numbers, company=company
    ).update(delivery_order_file=None, delivery_order_invoice_file=None)

    po_inv_count = do_count = doi_count = 0

    for po_number, m in metadata.items():
        if m["po_invoice_file"]:
            local = opex_docs / "purchase order invoice" / m["po_invoice_file"]
            key = "po/invoices/" + m["po_invoice_file"].replace(" ", "_")
            if _upload_po_file(local, key, po_number, "purchase_order_invoice_file", stdout):
                po_inv_count += 1

        if m["resi_file"]:
            local = opex_docs / "resi" / m["resi_file"]
            key = "po/delivery_orders/" + m["resi_file"].replace(" ", "_")
            if _upload_po_file(local, key, po_number, "delivery_order_file", stdout):
                do_count += 1

        if m["do_invoice_file"]:
            local = opex_docs / "invoice delivery file" / m["do_invoice_file"]
            key = "po/do_invoices/" + m["do_invoice_file"].replace(" ", "_")
            if _upload_po_file(local, key, po_number, "delivery_order_invoice_file", stdout):
                doi_count += 1

    # Attach RESI and DO invoice for split POs (second tracker row per split)
    by_no = _build_tracker_rows_by_no(po_tracker_wb)
    for cfg in SPLIT_DELIVERY_CONFIG:
        rows_for_no = by_no.get(cfg["tracker_no"], [])
        if len(rows_for_no) < 2:
            continue
        split_row = rows_for_no[1]
        split_po = cfg["split_po"]

        raw_do = str(split_row[4]) if split_row[4] else ""
        resi_fn = _clean_resi_filename(raw_do) if raw_do else ""
        if resi_fn:
            local = opex_docs / "resi" / resi_fn
            key = "po/delivery_orders/" + resi_fn.replace(" ", "_")
            if _upload_po_file(local, key, split_po, "delivery_order_file", stdout):
                do_count += 1

        raw_doi = str(split_row[19]) if split_row[19] else ""
        doi_fn = _clean_doi_filename(raw_doi) if raw_doi else ""
        if doi_fn:
            local = opex_docs / "invoice delivery file" / doi_fn
            key = "po/do_invoices/" + doi_fn.replace(" ", "_")
            if _upload_po_file(local, key, split_po, "delivery_order_invoice_file", stdout):
                doi_count += 1

    stdout.write(
        f"[OK] Attached: {po_inv_count} PO invoices, {do_count} resi, {doi_count} DO invoices\n"
    )


# ---------------------------------------------------------------------------
# Step 9 — Reconciliation report
# ---------------------------------------------------------------------------
def step_reconciliation(stdout: Any) -> None:
    if stdout is None:
        stdout = sys.stdout

    from apps.catalog.models import Product, ProductVariant
    from apps.finance.models import CashTransaction
    from apps.inventory.models import ProductCogs
    from apps.purchasing.models import ProductSupplier, PurchaseOrder, PurchaseOrderDetail
    from apps.sales.models import SalesOrder, SalesOrderItem

    checks: list[tuple[str, int]] = [
        ("Products", Product.objects.count()),
        ("Variants", ProductVariant.objects.count()),
        ("Purchase Orders", PurchaseOrder.objects.count()),
        ("PO Details", PurchaseOrderDetail.objects.count()),
        ("FIFO records", ProductCogs.objects.count()),
        ("Sales Orders", SalesOrder.objects.count()),
        ("Sales Items", SalesOrderItem.objects.count()),
        ("Cash Transactions", CashTransaction.objects.count()),
        (
            "POs with invoice_number",
            PurchaseOrder.objects.filter(invoice_number__isnull=False).count(),
        ),
        (
            "POs with delivery_order_number",
            PurchaseOrder.objects.filter(delivery_order_number__isnull=False).count(),
        ),
        (
            "POs with PO invoice file",
            PurchaseOrder.objects.filter(purchase_order_invoice_file__isnull=False)
            .exclude(purchase_order_invoice_file="")
            .count(),
        ),
        (
            "POs with resi file",
            PurchaseOrder.objects.filter(delivery_order_file__isnull=False)
            .exclude(delivery_order_file="")
            .count(),
        ),
        (
            "POs with DO invoice file",
            PurchaseOrder.objects.filter(delivery_order_invoice_file__isnull=False)
            .exclude(delivery_order_invoice_file="")
            .count(),
        ),
        (
            "Product-supplier links",
            ProductSupplier.objects.filter(supplier_link__isnull=False)
            .exclude(supplier_link="")
            .count(),
        ),
    ]

    neg_fifo = ProductCogs.objects.filter(remaining_qty__lt=0).count()
    checks.append(("Negative FIFO qty", neg_fifo))

    all_ok = True
    for label, count in checks:
        is_warn = label == "Negative FIFO qty" and count > 0
        status = "[WARN]" if is_warn else "[OK]  "
        if is_warn:
            all_ok = False
        stdout.write(f"  {status} {label}: {count}\n")

    stdout.write(f"\n{'All checks passed' if all_ok else 'Some warnings — review above'}\n")
