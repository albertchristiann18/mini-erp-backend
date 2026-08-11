from decimal import ROUND_FLOOR, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.inventory.models import ProductCogs
from apps.sales.models import SalesOrderCogsDetail, SalesOrderItem


class CogsConsumptionService:
    @transaction.atomic
    def consume_fifo(
        self, sales_order_item: SalesOrderItem, warehouse_id: object
    ) -> list[SalesOrderCogsDetail]:
        """
        Consume FIFO layers for a sales order item.
        Queries ProductCogs for variant+warehouse ordered by purchase_date ASC, cdate ASC.
        Deducts from oldest layer first, creates SalesOrderCogsDetail records.
        """
        if SalesOrderCogsDetail.objects.filter(sales_order_item=sales_order_item).exists():
            return list(SalesOrderCogsDetail.objects.filter(sales_order_item=sales_order_item))

        variant_id = sales_order_item.product_variant.pk
        qty_needed = sales_order_item.quantity

        cogs_layers = list(
            ProductCogs.objects.select_for_update()
            .filter(
                product_variant_id=variant_id,
                warehouse_id=warehouse_id,
                remaining_qty__gt=0,
                is_active=True,
            )
            .order_by("purchase_date", "cdate")
        )

        total_available = sum(layer.remaining_qty for layer in cogs_layers)
        if total_available < qty_needed:
            raise ValidationError(
                f"Insufficient COGS layers for variant {variant_id}. "
                f"Need {qty_needed}, available {total_available}."
            )

        details = []
        remaining_to_consume = qty_needed
        total_cogs_value = Decimal("0")

        for layer in cogs_layers:
            if remaining_to_consume <= 0:
                break

            consume_qty = min(remaining_to_consume, layer.remaining_qty)
            layer.remaining_qty -= consume_qty
            layer.save(update_fields=["remaining_qty", "udate"])

            layer_total_cogs = layer.cogs_amount * consume_qty
            cogs_detail = SalesOrderCogsDetail(
                company_id=sales_order_item.sales_order.company.pk,
                sales_order_item=sales_order_item,
                product_cogs=layer,
                quantity_consumed=consume_qty,
                cogs_per_unit=layer.cogs_amount,
                total_cogs=layer_total_cogs,
            )
            details.append(cogs_detail)
            total_cogs_value += layer_total_cogs
            remaining_to_consume -= consume_qty

        SalesOrderCogsDetail.objects.bulk_create(details, batch_size=100)

        # Calculate weighted average cogs_per_unit
        if qty_needed > 0:
            sales_order_item.actual_cogs_per_unit = (total_cogs_value / qty_needed).quantize(
                Decimal("0.01"), rounding=ROUND_FLOOR
            )
        sales_order_item.actual_cogs_total = total_cogs_value
        sales_order_item.save(update_fields=["actual_cogs_per_unit", "actual_cogs_total", "udate"])

        return details

    @transaction.atomic
    def reverse_fifo(self, sales_order_item: SalesOrderItem) -> None:
        """
        Reverse COGS consumption (for cancellation/return).
        Restores quantity back to ProductCogs layers and deletes detail records.
        """
        cogs_details = list(sales_order_item.cogs_details.select_related("product_cogs").all())

        for detail in cogs_details:
            cogs_layer = ProductCogs.objects.select_for_update().get(id=detail.product_cogs.pk)
            cogs_layer.remaining_qty += detail.quantity_consumed
            cogs_layer.save(update_fields=["remaining_qty", "udate"])

        SalesOrderCogsDetail.objects.filter(sales_order_item=sales_order_item).delete()

        sales_order_item.actual_cogs_per_unit = Decimal("0")
        sales_order_item.actual_cogs_total = Decimal("0")
        sales_order_item.save(update_fields=["actual_cogs_per_unit", "actual_cogs_total", "udate"])

    @transaction.atomic
    def partial_reverse_fifo(self, sales_order_item: SalesOrderItem, return_qty: int) -> Decimal:
        """
        Partially reverse COGS consumption for returns.
        Returns the total reversed COGS amount.
        Reverses from the most recently consumed layers first (LIFO reversal of FIFO consumption).
        """
        cogs_details = list(
            sales_order_item.cogs_details.select_related("product_cogs").order_by(
                "-product_cogs__purchase_date", "-product_cogs__cdate"
            )
        )

        remaining_to_reverse = return_qty
        total_reversed_cogs = Decimal("0")

        for detail in cogs_details:
            if remaining_to_reverse <= 0:
                break

            reverse_qty = min(remaining_to_reverse, detail.quantity_consumed)

            cogs_layer = ProductCogs.objects.select_for_update().get(id=detail.product_cogs.pk)
            cogs_layer.remaining_qty += reverse_qty
            cogs_layer.save(update_fields=["remaining_qty", "udate"])

            total_reversed_cogs += detail.cogs_per_unit * reverse_qty

            if reverse_qty == detail.quantity_consumed:
                detail.delete()
            else:
                detail.quantity_consumed -= reverse_qty
                detail.total_cogs = detail.cogs_per_unit * detail.quantity_consumed
                detail.save(update_fields=["quantity_consumed", "total_cogs", "udate"])

            remaining_to_reverse -= reverse_qty

        # Recalculate item COGS
        remaining_details = sales_order_item.cogs_details.all()
        new_total_cogs = sum((d.total_cogs for d in remaining_details), Decimal("0"))
        new_qty = sales_order_item.quantity - return_qty
        sales_order_item.actual_cogs_total = new_total_cogs
        sales_order_item.actual_cogs_per_unit = (
            (new_total_cogs / new_qty).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
            if new_qty > 0
            else Decimal("0")
        )
        sales_order_item.save(update_fields=["actual_cogs_per_unit", "actual_cogs_total", "udate"])

        return total_reversed_cogs
