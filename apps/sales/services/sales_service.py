from decimal import Decimal
from typing import Dict, List

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import ProductVariant
from apps.inventory.models import ProductVariantWarehouse, StockMovement
from apps.sales.models import (
    SalesOrder,
    SalesOrderItem,
    SalesReturn,
    SalesReturnItem,
)
from apps.sales.services.cogs_consumption import CogsConsumptionService
from core.utils import round_money_to_int


class SalesOrderService:
    STATUS_TRANSITIONS: Dict[str, List[str]] = {
        "PENDING": ["CONFIRMED", "CANCELLED"],
        "CONFIRMED": ["SHIPPING", "CANCELLED"],
        "SHIPPING": ["DELIVERED"],
        "DELIVERED": ["COMPLETED", "RETURNED"],
        "COMPLETED": [],
        "CANCELLED": [],
        "RETURNED": [],
    }

    @transaction.atomic
    def create_sales_order(self, data: dict) -> SalesOrder:
        """Create SO + items. Does NOT deduct stock (that happens on confirm)."""
        items_data = data.pop("items", [])
        warehouse_id = data.pop("warehouse_id")
        company_id = data.pop("company_id")
        marketplace_id = data.pop("marketplace_id", None)

        so = SalesOrder.objects.create(
            warehouse_id=warehouse_id,
            company_id=company_id,
            marketplace_id=marketplace_id,
            **data,
        )

        if items_data:
            order_items = []
            for item_data in items_data:
                if item_data.get("quantity", 0) <= 0:
                    raise ValidationError({"items": "Item quantity must be greater than zero."})
                if item_data.get("selling_price", 0) < 0:
                    raise ValidationError({"items": "Selling price cannot be negative."})
                product_variant_id = item_data.pop("product_variant_id")
                # Calculate line_total and total_marketplace_fee
                quantity = item_data.get("quantity", 0)
                selling_price = item_data.get("selling_price", 0)
                discount_amount = item_data.get("discount_amount", 0)
                commission_fee = item_data.get("commission_fee", 0)
                service_fee = item_data.get("service_fee", 0)

                item_data["line_total"] = (selling_price * quantity) - discount_amount
                item_data["total_marketplace_fee"] = commission_fee + service_fee

                order_items.append(
                    SalesOrderItem(
                        sales_order=so,
                        product_variant_id=product_variant_id,
                        company_id=company_id,
                        **item_data,
                    )
                )

            SalesOrderItem.objects.bulk_create(order_items, batch_size=100)

        self._recalculate_totals(so)
        return so

    @transaction.atomic
    def update_sales_order(self, so: SalesOrder, data: dict) -> SalesOrder:
        """Update SO fields. If status changes, validate transition and call appropriate handler."""
        new_status = data.get("status")

        if new_status and new_status != so.status:
            self._validate_transition(so.status, new_status)

            if new_status == SalesOrder.OrderStatus.CONFIRMED:
                return self.confirm_order(so)
            elif new_status == SalesOrder.OrderStatus.CANCELLED:
                return self.cancel_order(so)
            elif new_status == SalesOrder.OrderStatus.SHIPPING:
                so.status = SalesOrder.OrderStatus.SHIPPING
                so.shipped_date = data.get("shipped_date") or timezone.now()
                so.courier_name = data.get("courier_name", so.courier_name)
                so.tracking_number = data.get("tracking_number", so.tracking_number)
                so.save()
                return so
            elif new_status == SalesOrder.OrderStatus.DELIVERED:
                so.status = SalesOrder.OrderStatus.DELIVERED
                so.delivered_date = data.get("delivered_date") or timezone.now()
                so.save()
                return so
            elif new_status == SalesOrder.OrderStatus.COMPLETED:
                so.status = SalesOrder.OrderStatus.COMPLETED
                so.completed_date = data.get("completed_date") or timezone.now()
                so.save()

                # Create AccountsReceivable when SO moves to COMPLETED
                from apps.finance.services.accounts_payable_service import AccountsPayableService

                AccountsPayableService().create_receivable_from_so(so)

                return so
            elif new_status == SalesOrder.OrderStatus.RETURNED:
                so.status = SalesOrder.OrderStatus.RETURNED
                so.save()
                return so

        # Regular field update (no status change)
        update_fields = ["udate"]
        for attr, value in data.items():
            if attr not in ("id", "status", "items"):
                setattr(so, attr, value)
                update_fields.append(attr)
        so.save(update_fields=update_fields)
        return so

    def _validate_transition(self, current_status: str, new_status: str) -> None:
        allowed = self.STATUS_TRANSITIONS.get(current_status, [])
        if new_status not in allowed:
            raise ValidationError(
                {
                    "status": f"Cannot transition from {current_status} to {new_status}. "
                    f"Allowed transitions: {', '.join(allowed) if allowed else 'none'}"
                }
            )

    @transaction.atomic
    def confirm_order(self, so: SalesOrder) -> SalesOrder:
        """
        PENDING → CONFIRMED
        Deducts stock, consumes FIFO COGS, creates stock movements.
        """
        # Lock the row for update, then refresh the passed-in object
        SalesOrder.objects.select_for_update().get(id=so.id)
        so.refresh_from_db()
        self._validate_transition(so.status, SalesOrder.OrderStatus.CONFIRMED)

        items = list(so.items.select_related("product_variant").all())
        if not items:
            raise ValidationError({"items": "Cannot confirm an order with no items."})

        # Collect variant IDs for stock check
        variant_ids = [item.product_variant.pk for item in items]

        # Lock and check stock
        pvw_map = {
            str(pvw.product_variant.pk): pvw
            for pvw in ProductVariantWarehouse.objects.select_for_update().filter(
                warehouse_id=so.warehouse.pk,
                product_variant_id__in=variant_ids,
            )
        }

        for item in items:
            pvw = pvw_map.get(str(item.product_variant.pk))
            if not pvw:
                raise ValidationError(
                    f"No stock record found for variant {item.product_variant.pk} "
                    f"in warehouse {so.warehouse.pk}."
                )
            if pvw.available_qty < item.quantity:
                raise ValidationError(
                    f"Insufficient stock for variant {item.product_variant.name}. "
                    f"Available: {pvw.available_qty}, Required: {item.quantity}."
                )

        cogs_service = CogsConsumptionService()

        for item in items:
            # Consume FIFO COGS
            cogs_service.consume_fifo(item, so.warehouse.pk)

            # Deduct stock
            pvw = pvw_map[str(item.product_variant.pk)]
            pvw.physical_qty -= item.quantity
            pvw.save(update_fields=["physical_qty", "udate"])

            # Update variant total
            variant = ProductVariant.objects.select_for_update().get(id=item.product_variant.pk)
            variant.total_available_qty -= item.quantity
            variant.save(update_fields=["total_available_qty", "udate"])

            # Create stock movement
            StockMovement.objects.create(
                product_variant_id=item.product_variant.pk,
                warehouse_id=so.warehouse.pk,
                company_id=so.company.pk,
                quantity=item.quantity,
                movement_type=StockMovement.MovementType.OUTBOUND,
                balance_before=variant.total_available_qty + item.quantity,
                balance_after=variant.total_available_qty,
                reference_number=so.order_number,
                note=f"Sales order {so.order_number} confirmed",
            )

        so.status = SalesOrder.OrderStatus.CONFIRMED
        so.confirmed_date = timezone.now()
        so.save(update_fields=["status", "confirmed_date", "udate"])

        self._recalculate_totals(so)
        return so

    @transaction.atomic
    def cancel_order(self, so: SalesOrder) -> SalesOrder:
        """
        PENDING/CONFIRMED → CANCELLED
        If CONFIRMED: reverse stock deduction + reverse COGS consumption.
        """
        so = SalesOrder.objects.select_for_update().get(id=so.id)
        self._validate_transition(so.status, SalesOrder.OrderStatus.CANCELLED)

        if so.status == SalesOrder.OrderStatus.CONFIRMED:
            items = list(so.items.select_related("product_variant").all())
            cogs_service = CogsConsumptionService()

            for item in items:
                # Reverse COGS
                cogs_service.reverse_fifo(item)

                # Restore stock in warehouse
                pvw = ProductVariantWarehouse.objects.select_for_update().get(
                    warehouse_id=so.warehouse.pk,
                    product_variant_id=item.product_variant.pk,
                )
                pvw.physical_qty += item.quantity
                pvw.save(update_fields=["physical_qty", "udate"])

                # Restore variant total
                variant = ProductVariant.objects.select_for_update().get(id=item.product_variant.pk)
                variant.total_available_qty += item.quantity
                variant.save(update_fields=["total_available_qty", "udate"])

                # Create return stock movement
                StockMovement.objects.create(
                    product_variant_id=item.product_variant.pk,
                    warehouse_id=so.warehouse.pk,
                    company_id=so.company.pk,
                    quantity=item.quantity,
                    movement_type=StockMovement.MovementType.RETURN,
                    balance_before=variant.total_available_qty - item.quantity,
                    balance_after=variant.total_available_qty,
                    reference_number=so.order_number,
                    note=f"Sales order {so.order_number} cancelled - stock restored",
                )

        so.status = SalesOrder.OrderStatus.CANCELLED
        so.save(update_fields=["status", "udate"])

        self._recalculate_totals(so)
        return so

    def _recalculate_totals(self, so: SalesOrder) -> None:
        """Recalculate SO totals from items."""
        items = so.items.all()

        subtotal = Decimal("0")
        total_discount = Decimal("0")
        total_marketplace_fee = Decimal("0")
        total_cogs = Decimal("0")

        for item in items:
            subtotal += item.selling_price * item.quantity
            total_discount += item.discount_amount
            total_marketplace_fee += item.total_marketplace_fee
            total_cogs += item.actual_cogs_total

        so.subtotal = subtotal
        so.total_discount = total_discount
        so.total_marketplace_fee = total_marketplace_fee
        so.total_cogs = total_cogs
        so.net_revenue = subtotal - total_discount - total_marketplace_fee - so.shipping_fee_seller
        so.gross_profit = so.net_revenue - total_cogs
        so.save(
            update_fields=[
                "subtotal",
                "total_discount",
                "total_marketplace_fee",
                "total_cogs",
                "net_revenue",
                "gross_profit",
                "udate",
            ]
        )


class SalesReturnService:
    @transaction.atomic
    def create_return(self, sales_order: SalesOrder, data: dict) -> SalesReturn:
        """Create return + items. Validates qty doesn't exceed original - already returned."""
        items_data = data.pop("items", [])

        sales_return = SalesReturn.objects.create(
            sales_order=sales_order,
            company_id=sales_order.company.pk,
            **data,
        )

        # Get already returned quantities per sales_order_item
        existing_returns = {}
        for ri in SalesReturnItem.objects.filter(
            sales_return__sales_order=sales_order,
            sales_return__status__in=[
                SalesReturn.ReturnStatus.REQUESTED,
                SalesReturn.ReturnStatus.APPROVED,
                SalesReturn.ReturnStatus.RECEIVED,
            ],
        ).exclude(sales_return=sales_return):
            key = str(ri.sales_order_item.pk)
            existing_returns[key] = existing_returns.get(key, 0) + ri.quantity

        return_items = []
        for item_data in items_data:
            sales_order_item_id = item_data["sales_order_item_id"]
            so_item = SalesOrderItem.objects.get(id=sales_order_item_id)
            return_qty = item_data["quantity"]

            already_returned = existing_returns.get(str(sales_order_item_id), 0)
            max_returnable = so_item.quantity - already_returned

            if return_qty > max_returnable:
                raise ValidationError(
                    f"Return quantity ({return_qty}) exceeds maximum returnable "
                    f"({max_returnable}) for item {sales_order_item_id}."
                )

            return_items.append(
                SalesReturnItem(
                    sales_return=sales_return,
                    sales_order_item=so_item,
                    product_variant_id=so_item.product_variant.pk,
                    company_id=sales_order.company.pk,
                    quantity=return_qty,
                )
            )

        SalesReturnItem.objects.bulk_create(return_items, batch_size=100)
        return sales_return

    @transaction.atomic
    def receive_return(self, sales_return: SalesReturn) -> SalesReturn:
        """
        → RECEIVED
        Restores stock, partially reverses COGS, creates stock movements.
        """
        if sales_return.status == SalesReturn.ReturnStatus.RECEIVED:
            raise ValidationError("This return has already been received.")
        if sales_return.status != SalesReturn.ReturnStatus.APPROVED:
            raise ValidationError(
                f"Return must be APPROVED before receiving. Current status: {sales_return.status}"
            )

        sales_return.status = SalesReturn.ReturnStatus.RECEIVED
        sales_return.return_date = timezone.now()

        cogs_service = CogsConsumptionService()
        so = sales_return.sales_order

        return_items = list(
            sales_return.items.select_related("sales_order_item", "product_variant").all()
        )

        for return_item in return_items:
            # Partial COGS reversal
            reversed_cogs = cogs_service.partial_reverse_fifo(
                return_item.sales_order_item, return_item.quantity
            )
            return_item.reversed_cogs_total = reversed_cogs
            return_item.save(update_fields=["reversed_cogs_total", "udate"])

            # Restore warehouse stock
            pvw = ProductVariantWarehouse.objects.select_for_update().get(
                warehouse_id=so.warehouse.pk,
                product_variant_id=return_item.product_variant.pk,
            )
            pvw.physical_qty += return_item.quantity
            pvw.save(update_fields=["physical_qty", "udate"])

            # Restore variant total
            variant = ProductVariant.objects.select_for_update().get(
                id=return_item.product_variant.pk
            )
            variant.total_available_qty += return_item.quantity
            variant.save(update_fields=["total_available_qty", "udate"])

            # Create stock movement
            StockMovement.objects.create(
                product_variant_id=return_item.product_variant.pk,
                warehouse_id=so.warehouse.pk,
                company_id=so.company.pk,
                quantity=return_item.quantity,
                movement_type=StockMovement.MovementType.RETURN,
                balance_before=variant.total_available_qty - return_item.quantity,
                balance_after=variant.total_available_qty,
                reference_number=so.order_number,
                note=f"Sales return {sales_return.return_number} received",
            )

        sales_return.save(update_fields=["status", "return_date", "udate"])

        # Recalculate SO totals
        SalesOrderService()._recalculate_totals(so)

        # Update AR expected_amount after return
        so.refresh_from_db()
        try:
            ar = so.receivable
            # Calculate total refund from this return's items
            total_refund = sum(
                (ri.quantity * ri.sales_order_item.selling_price for ri in return_items),
                Decimal("0"),
            )
            # AccountsReceivable.expected_amount is still BigIntegerField (finance app,
            # not yet converted to Decimal) — round_money_to_int is the sanctioned
            # stopgap at this not-yet-converted boundary, same pattern MONEY-5 used
            # for the inventory→sales boundary.
            ar.expected_amount -= round_money_to_int(total_refund)
            ar.save(update_fields=["expected_amount", "udate"])
        except Exception:
            pass  # AR may not exist

        return sales_return
