from datetime import date

from django.db.models import Q, Sum

from apps.catalog.models import ProductVariant
from apps.inventory.models import StockMovement
from apps.sales.models import SalesOrderCogsDetail, SalesOrderItem


class StockReportService:
    def stock_movement_report(
        self,
        company_id: str,
        start_date: date,
        end_date: date,
        warehouse_id: str | None = None,
        product_variant_id: str | None = None,
    ) -> list[dict]:
        """Per variant stock movement report for a period."""
        variant_qs = ProductVariant.objects.filter(
            product__company_id=company_id,
            is_active=True,
        )
        if product_variant_id:
            variant_qs = variant_qs.filter(id=product_variant_id)

        movement_filters = Q(
            product_variant__product__company_id=company_id,
            cdate__date__gte=start_date,
            cdate__date__lte=end_date,
        )
        if warehouse_id:
            movement_filters &= Q(warehouse_id=warehouse_id)
        if product_variant_id:
            movement_filters &= Q(product_variant_id=product_variant_id)

        # Get movements aggregated per variant
        movements_by_variant = {}
        movement_qs = StockMovement.objects.filter(movement_filters)

        for variant_id in variant_qs.values_list("id", flat=True):
            variant_movements = movement_qs.filter(product_variant_id=variant_id)

            in_purchase = (
                variant_movements.filter(
                    movement_type=StockMovement.MovementType.INBOUND
                ).aggregate(total=Sum("quantity"))["total"]
                or 0
            )

            out_sales = abs(
                variant_movements.filter(
                    movement_type=StockMovement.MovementType.OUTBOUND
                ).aggregate(total=Sum("quantity"))["total"]
                or 0
            )

            adjustments = (
                variant_movements.filter(
                    movement_type=StockMovement.MovementType.ADJUSTMENT
                ).aggregate(total=Sum("quantity"))["total"]
                or 0
            )

            returns = (
                variant_movements.filter(movement_type=StockMovement.MovementType.RETURN).aggregate(
                    total=Sum("quantity")
                )["total"]
                or 0
            )

            movements_by_variant[str(variant_id)] = {
                "in_purchase": in_purchase,
                "out_sales": out_sales,
                "adjustments": adjustments,
                "returns": returns,
            }

        results = []
        for variant in variant_qs.select_related("product"):
            vid = str(variant.id)
            mvmt = movements_by_variant.get(vid, {})
            ending_qty = variant.total_available_qty
            net_movement = (
                mvmt.get("in_purchase", 0)
                - mvmt.get("out_sales", 0)
                + mvmt.get("adjustments", 0)
                + mvmt.get("returns", 0)
            )
            beginning_qty = ending_qty - net_movement

            results.append(
                {
                    "variant_id": vid,
                    "sku": variant.sku_variant_code,
                    "name": variant.name,
                    "beginning_qty": beginning_qty,
                    "in_purchase": mvmt.get("in_purchase", 0),
                    "out_sales": mvmt.get("out_sales", 0),
                    "adjustments": mvmt.get("adjustments", 0),
                    "returns": mvmt.get("returns", 0),
                    "ending_qty": ending_qty,
                    "ending_value": ending_qty * variant.current_cogs,
                }
            )

        return results

    def cogs_report(
        self,
        company_id: str,
        start_date: date,
        end_date: date,
        sales_order_id: str | None = None,
    ) -> list[dict]:
        """Per SalesOrderItem COGS report for a period."""
        item_qs = SalesOrderItem.objects.filter(
            sales_order__company_id=company_id,
            sales_order__order_date__date__gte=start_date,
            sales_order__order_date__date__lte=end_date,
        ).select_related("sales_order", "product_variant")

        if sales_order_id:
            item_qs = item_qs.filter(sales_order_id=sales_order_id)

        results = []
        for item in item_qs:
            fifo_details = SalesOrderCogsDetail.objects.filter(
                sales_order_item=item,
            ).select_related("product_cogs")
            fifo_layers = [
                {
                    "reference": d.product_cogs.reference_number,
                    "qty_consumed": d.quantity_consumed,
                    "cogs_per_unit": d.cogs_per_unit,
                    "total": d.total_cogs,
                }
                for d in fifo_details
            ]

            results.append(
                {
                    "order_number": item.sales_order.order_number,
                    "order_date": str(item.sales_order.order_date.date()),
                    "variant_sku": item.product_variant.sku_variant_code,
                    "variant_name": item.product_variant.name,
                    "quantity": item.quantity,
                    "selling_price": item.selling_price,
                    "actual_cogs_per_unit": item.actual_cogs_per_unit,
                    "actual_cogs_total": item.actual_cogs_total,
                    "fifo_layers": fifo_layers,
                }
            )

        return results
