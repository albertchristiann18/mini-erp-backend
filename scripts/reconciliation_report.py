#!/usr/bin/env python3
"""End-to-end reconciliation report for the full data migration."""

import os
import sys
from collections import defaultdict

import django

sys.path.insert(0, "/Users/jtf01644/personal/mini-erp-project/mini-erp-backend")
os.environ["DJANGO_SETTINGS_MODULE"] = "core.settings"
django.setup()

from django.db.models import Sum, Count, Q

from apps.inventory.models import (
    ProductVariant,
    ProductVariantWarehouse,
    ProductCogs,
    StockMovement,
)
from apps.purchasing.models import PurchaseOrder, PurchaseOrderDetail
from apps.sales.models import SalesOrder, SalesOrderItem
from apps.finance.models import CashTransaction
from core.models import Company

company = Company.objects.get(name="Mirako")


def sep(title: str) -> str:
    return f"\n{'=' * 72}\n  {title}\n{'=' * 72}"


def pass_fail(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


# ─────────────────────────────────────────────
# SECTION 1: PRODUCTS & VARIANTS
# ─────────────────────────────────────────────
print(sep("SECTION 1: PRODUCTS & VARIANTS"))

total_variants = ProductVariant.objects.filter(product__company=company).count()
active_variants = ProductVariant.objects.filter(
    product__company=company, is_active=True
).count()
print(f"  1a. ProductVariants: {total_variants} total, {active_variants} active")

total_pvw = ProductVariantWarehouse.objects.filter(
    product_variant__product__company=company
).count()
print(f"  1b. ProductVariantWarehouse records: {total_pvw}")

stock_qs = ProductVariantWarehouse.objects.filter(
    product_variant__product__company=company
)
total_physical_qty = stock_qs.aggregate(s=Sum("physical_qty"))["s"] or 0
negative_variants = (
    stock_qs.values("product_variant__sku_variant_code")
    .annotate(total_qty=Sum("physical_qty"))
    .filter(total_qty__lt=0)
)
neg_count = negative_variants.count()
neg_total = sum(abs(v["total_qty"]) for v in negative_variants)
print(f"  1c. Total physical_qty: {total_physical_qty}")
print(f"      Variants with negative stock: {neg_count}")
print(f"      Total deficit units: {neg_total}")

# ─────────────────────────────────────────────
# SECTION 2: PURCHASE ORDERS
# ─────────────────────────────────────────────
print(sep("SECTION 2: PURCHASE ORDERS"))

hist_pos = PurchaseOrder.objects.filter(
    company=company, purchase_order_number__startswith="HIST-"
)
hist_po_count = hist_pos.count()
print(f"  2a. HIST POs: {hist_po_count}")

hist_po_detail_count = PurchaseOrderDetail.objects.filter(
    purchase_order__company=company,
    purchase_order__purchase_order_number__startswith="HIST-",
).count()
print(f"  2b. PO Details (HIST): {hist_po_detail_count}")

fifo_batches = ProductCogs.objects.filter(
    company=company, reference_number__startswith="HIST-"
)
fifo_count = fifo_batches.count()
print(f"  2c. FIFO batches (HIST-): {fifo_count}")

hist_po_stock_movements = StockMovement.objects.filter(
    company=company,
    movement_type=StockMovement.MovementType.INBOUND,
    reference_number__startswith="HIST-",
)
hist_sm_count = hist_po_stock_movements.count()
print(f"  2d. StockMovements (INBOUND, HIST-): {hist_sm_count}")

pos_with_attachment = hist_pos.exclude(
    Q(purchase_order_invoice_file="") | Q(purchase_order_invoice_file__isnull=True)
).count()
pos_without_attachment = hist_po_count - pos_with_attachment
print(f"  2e. HIST POs with PDF attachment: {pos_with_attachment}")
print(f"      HIST POs without attachment: {pos_without_attachment}")

total_received_qty = (
    PurchaseOrderDetail.objects.filter(
        purchase_order__company=company,
        purchase_order__purchase_order_number__startswith="HIST-",
    ).aggregate(s=Sum("received_qty"))["s"]
    or 0
)
print(f"  2f. Total received_qty from HIST POs: {total_received_qty}")

# ─────────────────────────────────────────────
# SECTION 3: SALES ORDERS
# ─────────────────────────────────────────────
print(sep("SECTION 3: SALES ORDERS"))

so_total = SalesOrder.objects.filter(company=company).count()
so_by_status = dict(
    SalesOrder.objects.filter(company=company)
    .values("status")
    .annotate(c=Count("id"))
    .values_list("status", "c")
)
print(f"  3a. SalesOrders: {so_total}")
for status, count in sorted(so_by_status.items()):
    print(f"       - {status}: {count}")

so_items_total = SalesOrderItem.objects.filter(
    sales_order__company=company
).count()
print(f"  3b. SalesOrderItems: {so_items_total}")

cogs_fifo_consumed = SalesOrderItem.objects.filter(
    sales_order__company=company, actual_cogs_per_unit__gt=0
).count()
cogs_missing = SalesOrderItem.objects.filter(
    Q(sales_order__company=company),
    Q(actual_cogs_per_unit=0) | Q(actual_cogs_per_unit__isnull=True),
).count()
print(f"  3c. Items with FIFO consumed (actual_cogs_per_unit > 0): {cogs_fifo_consumed}")
print(f"      Items with missing FIFO (actual_cogs_per_unit == 0 or NULL): {cogs_missing}")

closed_statuses = ["COMPLETED", "SHIPPING"]
closed_orders = SalesOrder.objects.filter(
    company=company, status__in=closed_statuses
)
rev_agg = closed_orders.aggregate(
    total_subtotal=Sum("subtotal"),
    total_cogs_order=Sum("total_cogs"),
)
so_item_agg = (
    SalesOrderItem.objects.filter(
        sales_order__company=company,
        sales_order__status__in=closed_statuses,
    ).aggregate(
        total_actual_cogs=Sum("actual_cogs_total"),
    )
)
print(f"  3d. Revenue (subtotal, COMPLETED/SHIPPING): {rev_agg['total_subtotal'] or 0:,}")
print(f"      COGS total from SalesOrder.total_cogs: {rev_agg['total_cogs_order'] or 0:,}")
print(f"      COGS total from SalesOrderItem.actual_cogs_total: {so_item_agg['total_actual_cogs'] or 0:,}")

# ─────────────────────────────────────────────
# SECTION 4: CASH TRANSACTIONS
# ─────────────────────────────────────────────
print(sep("SECTION 4: CASH TRANSACTIONS"))

ct_total = CashTransaction.objects.filter(company=company).count()
ct_inflow = CashTransaction.objects.filter(
    company=company, transaction_type=CashTransaction.TransactionType.INFLOW
).count()
ct_outflow = CashTransaction.objects.filter(
    company=company, transaction_type=CashTransaction.TransactionType.OUTFLOW
).count()
print(f"  4a. CashTransactions: {ct_total} ({ct_inflow} INFLOW, {ct_outflow} OUTFLOW)")

ct_sum = CashTransaction.objects.filter(company=company).aggregate(
    inflow=Sum("amount", filter=Q(transaction_type="INFLOW")),
    outflow=Sum("amount", filter=Q(transaction_type="OUTFLOW")),
)
inflow_total = ct_sum["inflow"] or 0
outflow_total = ct_sum["outflow"] or 0
net_balance = inflow_total - outflow_total
print(f"  4b. INFLOW: {inflow_total:,} IDR")
print(f"      OUTFLOW: {outflow_total:,} IDR")
print(f"      Net balance: {net_balance:,} IDR")

cat_breakdown = (
    CashTransaction.objects.filter(company=company)
    .values("category")
    .annotate(cnt=Count("id"), total=Sum("amount"))
    .order_by("category")
)
print(f"  4c. Category breakdown:")
for row in cat_breakdown:
    print(f"       - {row['category']}: {row['cnt']} txns, {row['total']:,} IDR")

# ─────────────────────────────────────────────
# SECTION 5: CROSS-CHECKS
# ─────────────────────────────────────────────
print(sep("SECTION 5: CROSS-CHECKS"))

print("  5a. Stock balance sanity:")
inbound_qty = (
    StockMovement.objects.filter(
        company=company,
        movement_type=StockMovement.MovementType.INBOUND,
    ).aggregate(s=Sum("quantity"))["s"]
    or 0
)
outbound_qty = (
    StockMovement.objects.filter(
        company=company,
        movement_type=StockMovement.MovementType.OUTBOUND,
    ).aggregate(s=Sum("quantity"))["s"]
    or 0
)
# Also include PURCHASE type if it's separate from INBOUND
purchase_inbound_qty = (
    StockMovement.objects.filter(
        company=company,
        movement_type=StockMovement.MovementType.PURCHASE,
    ).aggregate(s=Sum("quantity"))["s"]
    or 0
)
current_stock = total_physical_qty  # already computed above
print(f"       INBOUND StockMovements qty: {inbound_qty:,}")
print(f"       PURCHASE StockMovements qty: {purchase_inbound_qty:,}")
print(f"       OUTBOUND StockMovements qty: {outbound_qty:,}")
print(f"       Current total physical_qty (PVW): {current_stock:,}")

print("  5b. FIFO batch coverage:")
fifo_hist = ProductCogs.objects.filter(
    company=company, reference_number__startswith="HIST-"
)
fifo_agg = fifo_hist.aggregate(
    total_original=Sum("original_qty"),
    total_remaining=Sum("remaining_qty"),
)
fifo_original = fifo_agg["total_original"] or 0
fifo_remaining = fifo_agg["total_remaining"] or 0
fifo_consumed = fifo_original - fifo_remaining
print(f"       SUM(original_qty) from HIST FIFO: {fifo_original}")
print(f"       SUM(remaining_qty) from HIST FIFO: {fifo_remaining}")
print(f"       Units consumed via FIFO: {fifo_consumed}")
negative_fifo_count = ProductCogs.objects.filter(
    company=company, remaining_qty__lt=0
).count()
print(f"       Negative remaining_qty batches: {negative_fifo_count}")

print("  5c. Top 10 negative stock variants:")
neg_top = (
    ProductVariantWarehouse.objects.filter(
        product_variant__product__company=company,
    )
    .values("product_variant__sku_variant_code")
    .annotate(total_qty=Sum("physical_qty"))
    .filter(total_qty__lt=0)
    .order_by("total_qty")[:10]
)
for v in neg_top:
    print(
        f"       {v['product_variant__sku_variant_code']}: {v['total_qty']}"
    )

# ─────────────────────────────────────────────
# SECTION 6: MIGRATION COMPLETENESS SUMMARY
# ─────────────────────────────────────────────
print(sep("SECTION 6: MIGRATION COMPLETENESS SUMMARY"))

checks = [
    ("HIST POs", 14, hist_po_count, hist_po_count == 14),
    ("PO Details", 491, hist_po_detail_count, hist_po_detail_count == 491),
    ("FIFO Batches", 491, fifo_count, fifo_count == 491),
    ("PO Receipt StockMovements", 491, hist_sm_count, hist_sm_count == 491),
    ("SalesOrders", ">= 4000", so_total, so_total >= 4000),
    ("SalesOrderItems", ">= 5000", so_items_total, so_items_total >= 5000),
    ("CashTransactions", 278, ct_total, ct_total == 278),
    ("HIST POs with PDF", 12, pos_with_attachment, pos_with_attachment == 12),
    ("Negative FIFO batches", 0, negative_fifo_count, negative_fifo_count == 0),
    ("Net cash balance", "> 0", net_balance, net_balance > 0),
]

print(f"  {'Check':<35} {'Expected':<15} {'Actual':<15} {'Status'}")
print(f"  {'-'*35} {'-'*15} {'-'*15} {'-'*6}")
for name, expected, actual, ok in checks:
    exp_str = str(expected)
    act_str = f"{actual:,}" if isinstance(actual, int) else str(actual)
    print(f"  {name:<35} {exp_str:<15} {act_str:<15} {pass_fail(ok)}")

all_pass = all(ok for _, _, _, ok in checks)
print(f"\n  Overall: {'ALL CHECKS PASS' if all_pass else 'SOME CHECKS FAILED'}")
sys.exit(0)
