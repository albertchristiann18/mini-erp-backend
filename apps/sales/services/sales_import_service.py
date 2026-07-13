"""
Service layer for importing historical Sales Orders from parsed Excel workbook sheets.

All DB writes are wrapped in a single @transaction.atomic call.
No ORM calls inside Python loops — all bulk operations use bulk_create / bulk_update.
Uses Decimal throughout for parsing; stores as int for BigIntegerField columns.

The SO number trigger (trg_generate_so_number) is suppressed via:
    SET LOCAL session_replication_role = 'replica'
which reverts automatically when the transaction ends.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import connection, transaction

from core.management.commands.import_sales_parser import ParsedSalesOrder

if TYPE_CHECKING:
    from apps.inventory.models import Warehouse
    from core.models import Company


@dataclass
class SalesImportResult:
    orders_created: int
    items_created: int
    orders_skipped_duplicate: int
    items_skipped_no_sku: int
    fifo_shortage_warnings: int


class SalesImportService:
    @transaction.atomic
    def import_sales(
        self,
        company: "Company",
        warehouse: "Warehouse",
        variant_map: dict[str, str],  # sku_code → str(variant_id)
        parsed_orders: list[ParsedSalesOrder],
    ) -> SalesImportResult:
        """
        Import parsed sales orders for a company into the database.

        Performs a full idempotency reset before inserting:
        - Deletes all SalesOrders (cascades to items and cogs details) for the company
        - Deletes all OUT StockMovements for the company's variants
        - Resets ProductCogs remaining_qty to original_qty
        - Resets ProductVariantWarehouse physical_qty from COGS sums, outgoing_qty=0
        - Resets ProductVariant total_outgoing_qty=0, total_available_qty from PVW sums

        Returns a SalesImportResult summary.
        """
        # Suppress trg_generate_so_number trigger so explicit order_numbers are preserved.
        # SET LOCAL reverts automatically when the transaction ends.
        with connection.cursor() as cur:
            cur.execute("SET LOCAL session_replication_role = 'replica'")

        # Inline imports to avoid circular dependency at module load time
        from django.db.models import F, IntegerField, OuterRef, Subquery, Sum
        from django.db.models.functions import Coalesce

        from apps.catalog.models import ProductVariant
        from apps.inventory.models import (
            ProductCogs,
            ProductVariantWarehouse,
            StockMovement,
        )
        from apps.sales.models import SalesOrder, SalesOrderItem
        from apps.sales.services.cogs_consumption import CogsConsumptionService

        # ------------------------------------------------------------------
        # Step 1: Idempotency reset (ORM, no raw SQL)
        # ------------------------------------------------------------------

        # Delete all sales data for this company (cascade covers SOI and CogsDetail)
        SalesOrder.objects.filter(company=company).delete()

        # Collect all variant IDs for this company (used in several subsequent queries)
        company_variant_ids = list(
            ProductVariant.objects.filter(company=company).values_list("id", flat=True)
        )

        # Delete OUT StockMovements for company's variants
        StockMovement.objects.filter(
            product_variant_id__in=company_variant_ids,
            movement_type=StockMovement.MovementType.OUTBOUND,
        ).delete()

        # Reset FIFO layers: remaining_qty = original_qty
        ProductCogs.objects.filter(company=company).update(remaining_qty=F("original_qty"))

        # Reset ProductVariantWarehouse: physical_qty from COGS sums, outgoing_qty=0
        ProductVariantWarehouse.objects.filter(product_variant__company=company).update(
            physical_qty=Coalesce(
                Subquery(
                    ProductCogs.objects.filter(
                        product_variant_id=OuterRef("product_variant_id"),
                        warehouse_id=OuterRef("warehouse_id"),
                    )
                    .values("product_variant_id")
                    .annotate(total=Sum("original_qty"))
                    .values("total")[:1],
                    output_field=IntegerField(),
                ),
                0,
            ),
            outgoing_qty=0,
        )

        # Reset ProductVariant totals: total_outgoing_qty=0, total_available_qty from PVW sums
        ProductVariant.objects.filter(company=company).update(
            total_outgoing_qty=0,
            total_available_qty=Coalesce(
                Subquery(
                    ProductVariantWarehouse.objects.filter(product_variant_id=OuterRef("id"))
                    .values("product_variant_id")
                    .annotate(total=Sum("physical_qty"))
                    .values("total")[:1],
                    output_field=IntegerField(),
                ),
                0,
            ),
        )

        # ------------------------------------------------------------------
        # Step 2: Build SalesOrder objects (no save yet), deduplicating by order_number
        # ------------------------------------------------------------------
        seen_order_numbers: set[str] = set()
        so_list: list[SalesOrder] = []
        # Track per-order metadata needed later
        so_is_stock_affecting: dict[str, bool] = {}  # order_number → bool
        so_parsed_map: dict[str, ParsedSalesOrder] = {}  # order_number → parsed order
        orders_skipped_duplicate = 0
        items_skipped_no_sku = 0

        for order in parsed_orders:
            if order.order_number in seen_order_numbers:
                orders_skipped_duplicate += 1
                continue
            seen_order_numbers.add(order.order_number)
            so_is_stock_affecting[order.order_number] = order.is_stock_affecting
            so_parsed_map[order.order_number] = order

            so = SalesOrder(
                order_number=order.order_number,
                marketplace_order_id=order.order_number,
                marketplace_order_number=order.order_number,
                status=order.status,
                source_platform=SalesOrder.SourcePlatform.SHOPEE,
                warehouse=warehouse,
                company=company,
                marketplace=None,
                customer_name=order.customer_name,
                courier_name=order.courier_name,
                tracking_number=order.tracking_number,
                order_date=order.order_date,
                subtotal=order.subtotal,
                net_revenue=order.net_revenue,
                total_cogs=0,
                gross_profit=0,
                note=f"Imported from {order.sheet_name}",
            )
            so_list.append(so)

        # ------------------------------------------------------------------
        # Step 3: Bulk create SalesOrders
        # ------------------------------------------------------------------
        created_sos = SalesOrder.objects.bulk_create(so_list, batch_size=100)

        # ------------------------------------------------------------------
        # Step 4: Build SalesOrderItem objects
        # ------------------------------------------------------------------
        item_list: list[SalesOrderItem] = []
        # Track which SO each item belongs to (parallel list with item_list)
        item_order_numbers: list[str] = []

        for so in created_sos:
            parsed = so_parsed_map[so.order_number]
            for parsed_item in parsed.items:
                variant_id = variant_map.get(parsed_item.sku_code)
                if variant_id is None:
                    items_skipped_no_sku += 1
                    continue

                soi = SalesOrderItem(
                    sales_order=so,
                    product_variant_id=variant_id,
                    company=company,
                    quantity=parsed_item.quantity,
                    selling_price=parsed_item.selling_price,
                    line_total=parsed_item.line_total,
                    actual_cogs_per_unit=0,
                    actual_cogs_total=0,
                )
                item_list.append(soi)
                item_order_numbers.append(so.order_number)

        # ------------------------------------------------------------------
        # Step 5: Bulk create SalesOrderItems
        # ------------------------------------------------------------------
        created_items = SalesOrderItem.objects.bulk_create(item_list, batch_size=100)

        # ------------------------------------------------------------------
        # Step 6: Load current balances after reset (one query)
        # Store objects for reuse in Step 8 — no second queryset.
        # ------------------------------------------------------------------
        pvw_objects: list[ProductVariantWarehouse] = list(
            ProductVariantWarehouse.objects.filter(
                product_variant_id__in=company_variant_ids,
                warehouse=warehouse,
            ).select_related("product_variant")
        )
        balance_tracker: dict[str, int] = {
            str(pvw.product_variant.pk): pvw.physical_qty for pvw in pvw_objects
        }
        pvw_by_variant: dict[str, ProductVariantWarehouse] = {
            str(pvw.product_variant.pk): pvw for pvw in pvw_objects
        }

        # ------------------------------------------------------------------
        # Step 7: FIFO consumption and stock tracking
        # ------------------------------------------------------------------
        cogs_service = CogsConsumptionService()
        variant_out_qty: dict[str, int] = {}
        movements_to_create: list[StockMovement] = []
        fifo_shortage_warnings = 0

        # Per-SO COGS accumulator: order_number → total cogs
        so_total_cogs: dict[str, int] = {so.order_number: 0 for so in created_sos}

        # Refresh created_items with select_related so .product_variant.pk doesn't hit DB per item
        created_items_with_pv = list(
            SalesOrderItem.objects.filter(id__in=[it.pk for it in created_items]).select_related(
                "product_variant"
            )
        )
        # Rebuild item_order_numbers to match the refreshed order (bulk_create preserves insertion order)
        item_pk_to_order_number: dict[str, str] = {
            str(it.pk): on for it, on in zip(created_items, item_order_numbers)
        }

        for item in created_items_with_pv:
            order_number = item_pk_to_order_number[str(item.pk)]
            variant_id = str(item.product_variant.pk)
            is_stock_affecting = so_is_stock_affecting.get(order_number, False)

            if is_stock_affecting:
                item_cogs_total = 0
                try:
                    details = cogs_service.consume_fifo(item, warehouse.id)
                    item_cogs_total = sum(d.total_cogs for d in details)
                except ValidationError:
                    fifo_shortage_warnings += 1
                    # item keeps actual_cogs_per_unit=0, actual_cogs_total=0 (defaults)

                so_total_cogs[order_number] = so_total_cogs.get(order_number, 0) + item_cogs_total

                qty = item.quantity
                bal_before = balance_tracker.get(variant_id, 0)
                bal_after = bal_before - qty
                balance_tracker[variant_id] = bal_after
                variant_out_qty[variant_id] = variant_out_qty.get(variant_id, 0) + qty

                movements_to_create.append(
                    StockMovement(
                        product_variant_id=variant_id,
                        warehouse=warehouse,
                        company=company,
                        movement_type=StockMovement.MovementType.OUTBOUND,
                        field_change="physical_qty",
                        quantity=-qty,
                        reference_number=order_number,
                        note="Sale: imported",
                        balance_before=bal_before,
                        balance_after=bal_after,
                    )
                )

        # ------------------------------------------------------------------
        # Step 8: Bulk update PVW and ProductVariant
        # Reuse pvw_by_variant from Step 6 — no second query on this table.
        # ------------------------------------------------------------------
        if variant_out_qty:
            pvws_to_update: list[ProductVariantWarehouse] = []
            for vid, qty_out in variant_out_qty.items():
                pvw = pvw_by_variant.get(vid)
                if pvw:
                    pvw.physical_qty -= qty_out
                    pvw.outgoing_qty += qty_out
                    pvws_to_update.append(pvw)
            ProductVariantWarehouse.objects.bulk_update(
                pvws_to_update, ["physical_qty", "outgoing_qty"], batch_size=100
            )

            variants = list(ProductVariant.objects.filter(id__in=variant_out_qty.keys()))
            for v in variants:
                vid = str(v.id)
                v.total_outgoing_qty += variant_out_qty.get(vid, 0)
                v.total_available_qty -= variant_out_qty.get(vid, 0)
            ProductVariant.objects.bulk_update(
                variants, ["total_outgoing_qty", "total_available_qty"], batch_size=100
            )

        # ------------------------------------------------------------------
        # Step 9: Bulk create StockMovements
        # ------------------------------------------------------------------
        StockMovement.objects.bulk_create(movements_to_create, batch_size=100)

        # ------------------------------------------------------------------
        # Step 10: Compute SO totals and bulk_update
        # ------------------------------------------------------------------
        sos_to_update: list[SalesOrder] = []
        for so in created_sos:
            cogs = so_total_cogs.get(so.order_number, 0)
            so.total_cogs = cogs
            so.gross_profit = so.net_revenue - cogs
            sos_to_update.append(so)

        if sos_to_update:
            SalesOrder.objects.bulk_update(
                sos_to_update, ["total_cogs", "gross_profit"], batch_size=100
            )

        return SalesImportResult(
            orders_created=len(created_sos),
            items_created=len(created_items),
            orders_skipped_duplicate=orders_skipped_duplicate,
            items_skipped_no_sku=items_skipped_no_sku,
            fifo_shortage_warnings=fifo_shortage_warnings,
        )
