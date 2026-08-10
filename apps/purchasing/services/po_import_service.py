"""
Service layer for importing historical Purchase Orders from parsed Excel workbook sheets.

All DB writes are wrapped in a single @transaction.atomic call.
No ORM calls inside Python loops — all bulk operations are performed outside loops.
Uses Decimal throughout; never float for money fields.

IMPORTANT: ALTER TABLE ... DISABLE/ENABLE TRIGGER is a DDL statement that must be executed
outside the atomic transaction block (PostgreSQL raises ObjectInUse if attempted inside
a transaction with pending trigger events). The trigger is disabled/enabled in a separate
cursor scope that wraps the atomic call.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from django.db import connection, transaction

from core.parsers.import_purchase_orders_parser import ParsedPoSheet
from core.utils import round_money_to_int

if TYPE_CHECKING:
    from apps.inventory.models import Warehouse
    from apps.purchasing.models import Supplier
    from core.models import Company


@dataclass
class PoImportResult:
    sheets_imported: int
    sheets_skipped: int
    pos_created: int
    details_created: int
    cogs_created: int
    movements_created: int


class PurchaseOrderImportService:
    def import_purchase_orders(
        self,
        company: "Company",
        warehouse: "Warehouse",
        supplier: "Supplier",
        parsed_sheets: list[ParsedPoSheet],
        variant_map: dict[str, str],  # sku_variant_code → variant_id (str)
        po_year_counter: dict[int, int] | None = None,
    ) -> PoImportResult:
        return self._run_import(
            company=company,
            warehouse=warehouse,
            supplier=supplier,
            parsed_sheets=parsed_sheets,
            variant_map=variant_map,
            po_year_counter=po_year_counter if po_year_counter is not None else {},
        )

    @transaction.atomic
    def _run_import(
        self,
        company: "Company",
        warehouse: "Warehouse",
        supplier: "Supplier",
        parsed_sheets: list[ParsedPoSheet],
        variant_map: dict[str, str],
        po_year_counter: dict[int, int],
    ) -> PoImportResult:
        # Suppress trg_generate_po_number trigger so explicit PO numbers are preserved.
        # SET LOCAL reverts automatically when the transaction ends (COMMIT or ROLLBACK).
        with connection.cursor() as cur:
            cur.execute("SET LOCAL session_replication_role = 'replica'")

        # Inline imports to avoid circular dependency at module load time
        from apps.inventory.models import (
            ProductCogs,
            ProductVariantWarehouse,
            StockMovement,
        )
        from apps.purchasing.models import PurchaseOrder, PurchaseOrderDetail

        # ------------------------------------------------------------------ #
        # Step 1: Generate all PO numbers up-front                           #
        # ------------------------------------------------------------------ #
        year_counter: dict[int, int] = dict(po_year_counter)

        sheet_po_numbers: list[str] = []
        for sheet in parsed_sheets:
            year = sheet.po_date.year
            next_val = year_counter.get(year, 0) + 1
            year_counter[year] = next_val
            po_number = f"PO-{year}-{next_val:03d}"
            sheet_po_numbers.append(po_number)

        all_po_numbers = sheet_po_numbers

        # ------------------------------------------------------------------ #
        # Step 2: Idempotency — delete existing data for these PO numbers    #
        # ------------------------------------------------------------------ #
        affected_variant_ids: set[str] = set()
        for sheet in parsed_sheets:
            for row in sheet.rows:
                vid = variant_map.get(row.sku_variant_code)
                if vid:
                    affected_variant_ids.add(vid)

        StockMovement.objects.filter(
            reference_number__in=all_po_numbers,
            company=company,
        ).delete()

        ProductCogs.objects.filter(
            reference_number__in=all_po_numbers,
            warehouse=warehouse,
        ).delete()

        existing_po_ids = list(
            PurchaseOrder.objects.filter(
                purchase_order_number__in=all_po_numbers,
                company=company,
            ).values_list("id", flat=True)
        )
        if existing_po_ids:
            PurchaseOrderDetail.objects.filter(purchase_order_id__in=existing_po_ids).delete()

        PurchaseOrder.objects.filter(
            purchase_order_number__in=all_po_numbers,
            company=company,
        ).delete()

        # Reset physical_qty for all affected variants in this warehouse to 0
        if affected_variant_ids:
            ProductVariantWarehouse.objects.filter(
                product_variant_id__in=affected_variant_ids,
                warehouse=warehouse,
            ).update(physical_qty=0)

        # ------------------------------------------------------------------ #
        # Step 3: Build all ORM objects in Python, then bulk_create          #
        # ------------------------------------------------------------------ #
        pos_to_create: list[PurchaseOrder] = []
        details_to_create: list[PurchaseOrderDetail] = []
        cogs_to_create: list[ProductCogs] = []
        movements_to_create: list[StockMovement] = []

        # balance tracking: variant_id → running physical_qty (Python dict, no DB reads)
        balance_tracker: dict[str, int] = {}
        # qty accumulator: variant_id → total qty added (for final bulk_update)
        variant_qty_added: dict[str, int] = {}
        # per-PO totals
        po_totals: dict[str, dict] = {}

        for sheet, po_number in zip(parsed_sheets, sheet_po_numbers):
            exchange_rate = sheet.exchange_rate
            po_date = sheet.po_date

            po = PurchaseOrder(
                purchase_order_number=po_number,
                status=PurchaseOrder.POStatus.COMPLETED,
                invoice_date=po_date,
                delivery_date=po_date,
                warehouse=warehouse,
                supplier=supplier,
                supplier_name=supplier.name,
                company=company,
                currency="CNY",
                exchange_rate=Decimal(str(exchange_rate)),
                has_discount=False,
                total_ordered_qty=0,
                total_received_qty=0,
                total_item_amount=0,
                procure_amount=0,
                shipping_fee=0,
                total_order_amount=0,
                total_amount=0,
            )
            pos_to_create.append(po)

            total_ordered_qty = 0
            total_price_base_sum = Decimal("0")

            for row in sheet.rows:
                variant_id = variant_map.get(row.sku_variant_code)
                if not variant_id:
                    continue
                if row.order_qty <= 0:
                    continue

                unit_price_rmb = row.unit_price_rmb
                unit_price_base = Decimal(round(unit_price_rmb * Decimal(str(exchange_rate))))
                total_price_foreign = unit_price_rmb * Decimal(str(row.order_qty))
                total_price_base = unit_price_base * row.order_qty

                detail = PurchaseOrderDetail(
                    purchase_order=po,
                    product_variant_id=variant_id,
                    company=company,
                    ordered_qty=row.order_qty,
                    received_qty=row.order_qty,
                    updated_qty=0,
                    unit_price_foreign=unit_price_rmb,
                    unit_price_base=unit_price_base,
                    total_price_foreign=total_price_foreign,
                    total_price_base=total_price_base,
                    discounted_unit_price_foreign=unit_price_rmb,
                    discounted_unit_price_base=unit_price_base,
                    discounted_total_price_foreign=total_price_foreign,
                    discounted_total_price_base=total_price_base,
                    stock_on_hand=row.stock_on_hand or 0,
                    incoming_qty=0,
                )
                details_to_create.append(detail)

                cogs = ProductCogs(
                    product_variant_id=variant_id,
                    warehouse=warehouse,
                    company=company,
                    reference_number=po_number,
                    purchase_date=po_date,
                    price_rmb=unit_price_rmb,
                    exchange_rate=exchange_rate,
                    cogs_amount=round_money_to_int(unit_price_base),
                    allocated_shipping_fee=0,
                    allocated_delivery_fee=0,
                    allocated_commission_fee=0,
                    original_qty=row.order_qty,
                    remaining_qty=row.order_qty,
                    is_active=True,
                )
                cogs_to_create.append(cogs)

                # Balance tracking in Python dict (no DB reads in loop)
                balance_before = balance_tracker.get(variant_id, 0)
                balance_after = balance_before + row.order_qty
                balance_tracker[variant_id] = balance_after

                movement = StockMovement(
                    product_variant_id=variant_id,
                    warehouse=warehouse,
                    company=company,
                    movement_type="IN",
                    field_change="physical_qty",
                    quantity=row.order_qty,
                    reference_number=po_number,
                    note=f"PO import: {sheet.sheet_name}",
                    balance_before=balance_before,
                    balance_after=balance_after,
                )
                movements_to_create.append(movement)

                variant_qty_added[variant_id] = variant_qty_added.get(variant_id, 0) + row.order_qty

                total_ordered_qty += row.order_qty
                total_price_base_sum += total_price_base

            po_totals[po_number] = {
                "total_ordered_qty": total_ordered_qty,
                "total_received_qty": total_ordered_qty,
                "total_item_amount": total_price_base_sum,
                "procure_amount": total_price_base_sum,
                "total_order_amount": total_price_base_sum,
                "total_amount": total_price_base_sum,
            }

        # ------------------------------------------------------------------ #
        # Step 4: Bulk create all objects                                     #
        # ------------------------------------------------------------------ #
        created_pos = PurchaseOrder.objects.bulk_create(pos_to_create, batch_size=100)
        created_details = PurchaseOrderDetail.objects.bulk_create(details_to_create, batch_size=100)
        created_cogs = ProductCogs.objects.bulk_create(cogs_to_create, batch_size=100)
        created_movements = StockMovement.objects.bulk_create(movements_to_create, batch_size=100)

        # ------------------------------------------------------------------ #
        # Step 5: Update PO totals                                            #
        # ------------------------------------------------------------------ #
        pos_to_update: list[PurchaseOrder] = []
        for po in created_pos:
            totals = po_totals.get(po.purchase_order_number)
            if totals:
                po.total_ordered_qty = totals["total_ordered_qty"]
                po.total_received_qty = totals["total_received_qty"]
                po.total_item_amount = totals["total_item_amount"]
                po.procure_amount = totals["procure_amount"]
                po.total_order_amount = totals["total_order_amount"]
                po.total_amount = totals["total_amount"]
                pos_to_update.append(po)

        if pos_to_update:
            PurchaseOrder.objects.bulk_update(
                pos_to_update,
                fields=[
                    "total_ordered_qty",
                    "total_received_qty",
                    "total_item_amount",
                    "procure_amount",
                    "total_order_amount",
                    "total_amount",
                ],
                batch_size=100,
            )

        # ------------------------------------------------------------------ #
        # Step 6: Update ProductVariantWarehouse physical_qty                 #
        # ------------------------------------------------------------------ #
        if variant_qty_added:
            pvw_qs = ProductVariantWarehouse.objects.filter(
                product_variant_id__in=variant_qty_added.keys(),
                warehouse=warehouse,
            )
            pvw_to_update: list[ProductVariantWarehouse] = []
            existing_pvw_ids: set[str] = set()

            for pvw in pvw_qs.select_related("product_variant"):
                vid = str(pvw.product_variant.pk)
                existing_pvw_ids.add(vid)
                pvw.physical_qty = variant_qty_added.get(vid, 0)
                pvw_to_update.append(pvw)

            if pvw_to_update:
                ProductVariantWarehouse.objects.bulk_update(
                    pvw_to_update, fields=["physical_qty"], batch_size=100
                )

            # Create missing ProductVariantWarehouse rows
            missing_ids = set(variant_qty_added.keys()) - existing_pvw_ids
            if missing_ids:
                new_pvw = [
                    ProductVariantWarehouse(
                        product_variant_id=vid,
                        warehouse=warehouse,
                        company=company,
                        physical_qty=variant_qty_added[vid],
                        incoming_qty=0,
                        outgoing_qty=0,
                        checkout_qty=0,
                    )
                    for vid in missing_ids
                ]
                ProductVariantWarehouse.objects.bulk_create(new_pvw, batch_size=100)

        # ------------------------------------------------------------------ #
        # Step 7: Seed po_number_counter                                      #
        # ------------------------------------------------------------------ #
        # company.id is a ULID — use .uuid to get the UUID object for the raw SQL
        # (po_number_counter.company_id is typed as UUID in PostgreSQL)
        company_uuid = company.id.uuid
        with connection.cursor() as cur:
            for year, last_val in year_counter.items():
                cur.execute(
                    "INSERT INTO po_number_counter (company_id, year, last_value) "
                    "VALUES (%s, %s, %s) "
                    "ON CONFLICT (company_id, year) DO UPDATE SET last_value = EXCLUDED.last_value",
                    [company_uuid, str(year), last_val],
                )

        return PoImportResult(
            sheets_imported=len(parsed_sheets),
            sheets_skipped=0,
            pos_created=len(created_pos),
            details_created=len(created_details),
            cogs_created=len(created_cogs),
            movements_created=len(created_movements),
        )
