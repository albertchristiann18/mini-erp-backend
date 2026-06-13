import logging
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django_ulid.models import ULIDField

logger = logging.getLogger(__name__)

from apps.inventory.models import (
    ProductCogs,
    ProductVariant,
    ProductVariantWarehouse,
    StockMovement,
)
from apps.purchasing.models import PurchaseOrder


class InventoryService:
    @transaction.atomic
    def record_single_stock_movement(
        self,
        variant_id: int,
        warehouse_id: int,
        qty: int,  # need to be absolute value (not negative)
        movement_type: str,
        reference_number: str | None = None,
        note: str | None = None,
    ) -> None:
        """
        Args:
            variant_id (int): product_variant_id
            warehouse (Warehouse): the warehouse where this movement happens
            qty (int): quantity
            reference_number (str, optional): reference number can refer to po_number, or invoice_number etc.
            note (str, optional): notes / remarks for each movement

        Notes:
            - for movement_type = ADJUSTMENT, qty can be inpputed positive or negative
            - for movement_type = TRANSFER, handled outside this method
        """
        if movement_type == StockMovement.MovementType.TRANSFER:
            return  # skip transfer type

        variant = (
            ProductVariant.objects.select_for_update()
            .only("id", "total_available_qty", "company_id")
            .get(id=variant_id)
        )
        pvw = (
            ProductVariantWarehouse.objects.select_for_update()
            .filter(product_variant=variant, warehouse_id=warehouse_id)
            .only(
                "id",
                "incoming_qty",
                "physical_qty",
                "product_variant_id",
                "warehouse_id",
                "company_id",
            )
            .last()
        )
        if not pvw:
            pvw = ProductVariantWarehouse.objects.create(
                product_variant=variant,
                warehouse_id=warehouse_id,
            )

        company_id = variant.company_id  # type: ignore[attr-defined]

        if movement_type == StockMovement.MovementType.PURCHASE:
            pvw.incoming_qty += qty
            pvw.save(update_fields=["incoming_qty"])
        elif movement_type in (
            StockMovement.MovementType.OUTBOUND,
            StockMovement.MovementType.INBOUND,
            StockMovement.MovementType.ADJUSTMENT,
        ):
            if movement_type == StockMovement.MovementType.OUTBOUND:
                if pvw.available_qty < qty:
                    raise ValidationError(
                        f"Insufficient stock for variant {variant_id}. "
                        f"Available: {pvw.available_qty}, requested: {qty}"
                    )
                stock_qty = -qty
                bal_before = pvw.physical_qty
                pvw.physical_qty -= qty
            else:
                stock_qty = qty
                bal_before = pvw.physical_qty
                pvw.physical_qty += qty
            pvw.save(update_fields=["physical_qty"])
            StockMovement.objects.create(
                company_id=company_id,
                product_variant=variant,
                warehouse_id=warehouse_id,
                movement_type=movement_type,
                quantity=stock_qty,
                reference_number=reference_number or "",
                note=note or "",
                balance_before=bal_before,
                balance_after=pvw.physical_qty,
                field_change="physical_qty",
            )
        self._trigger_shopee_sync(str(variant.id), str(company_id))

    def get_avg_sales_per_day(
        self,
        variant_ids: list[str],
        days: int,
    ) -> list[dict]:
        from datetime import timedelta

        from django.db.models import Sum
        from django.utils import timezone

        from apps.inventory.models import ProductVariant
        from apps.sales.models import SalesOrder, SalesOrderItem

        date_from = timezone.now().date() - timedelta(days=days)

        valid_statuses = [
            SalesOrder.OrderStatus.CONFIRMED,
            SalesOrder.OrderStatus.SHIPPING,
            SalesOrder.OrderStatus.DELIVERED,
            SalesOrder.OrderStatus.COMPLETED,
        ]

        items = (
            SalesOrderItem.objects.filter(
                product_variant_id__in=variant_ids,
                sales_order__status__in=valid_statuses,
                sales_order__order_date__date__gte=date_from,
            )
            .values("product_variant_id")
            .annotate(total_qty=Sum("quantity"))
        )
        qty_map: dict[str, int] = {
            str(item["product_variant_id"]): item["total_qty"] for item in items
        }

        variants = ProductVariant.objects.filter(id__in=variant_ids).values(
            "id", "sku_variant_code", "name"
        )

        result = []
        for variant in variants:
            vid = str(variant["id"])
            total_qty = qty_map.get(vid, 0)
            avg = round(total_qty / days, 3) if days > 0 else 0.0
            result.append(
                {
                    "variant_id": vid,
                    "sku_variant_code": variant["sku_variant_code"],
                    "variant_name": variant["name"],
                    "avg_sales_per_day": avg,
                    "total_qty_sold": total_qty,
                    "days": days,
                }
            )

        id_order = {vid: i for i, vid in enumerate(variant_ids)}
        result.sort(key=lambda r: id_order.get(str(r["variant_id"]), 9999))
        return result

    def _trigger_shopee_sync(self, variant_id: str, company_id: str) -> None:
        from apps.omnichannel.vendor.shopee.stock_sync import ShopeeStockSyncService
        from core.models import MarketplaceConnection

        connections = MarketplaceConnection.objects.filter(
            platform="SHOPEE",
            is_active=True,
            company_id=company_id,
        ).select_related("shopee_shop")

        if not connections.exists():
            return

        service = ShopeeStockSyncService()
        for connection in connections:
            if connection.shopee_shop:
                try:
                    service.sync_single_variant(variant_id, connection.shopee_shop)
                except Exception:
                    logger.warning(
                        "Shopee sync trigger failed for variant %s on shop %s",
                        variant_id,
                        connection.shopee_shop.shop_id,
                        exc_info=True,
                    )

    @transaction.atomic
    def record_multiple_stock_movements(
        self,
        warehouse_id: str,
        company_id: str,
        data: list[dict],
        map_product_variant: dict,
        reference_number: str | None = None,
        movement_type: str | None = None,
    ) -> None:
        """Create stock movement records.

        Args:
            warehouse_id: The warehouse ID
            data: List of dicts with product_variant_id, qty, note, field_change, qty_before
            map_product_variant: Dict mapping id -> ProductVariant.
            reference_number: Optional reference (e.g., PO number)
            movement_type: Type of movement (PURCHASE, INBOUND, etc.).
        """
        stock_movements: list[StockMovement] = []
        for item in data:
            product_variant_id = item.get("product_variant_id")
            pv = map_product_variant.get(product_variant_id)
            if not pv:
                pv = map_product_variant.get(str(product_variant_id))
            if not pv:
                pv_id = ULIDField().to_python(product_variant_id)
                pv = map_product_variant.get(pv_id)
            if not pv:
                pv = map_product_variant.get(str(pv_id))
            if not pv:
                raise ValidationError(f"ProductVariant with id {product_variant_id} not found")

            qty = item.get("qty", 0)
            field_change = item.get("field_change") or ""
            balance_before = item.get("qty_before", 0)
            balance_after = balance_before + qty

            stock_movements.append(
                StockMovement(
                    company_id=company_id,
                    product_variant_id=product_variant_id,
                    warehouse_id=warehouse_id,
                    quantity=qty,
                    field_change=field_change,
                    movement_type=movement_type or "",
                    balance_before=balance_before,
                    balance_after=balance_after,
                    reference_number=reference_number or "",
                    note=item.get("note"),
                )
            )

        StockMovement.objects.bulk_create(stock_movements, batch_size=100)

    @transaction.atomic
    def update_stock_on_po(
        self,
        po: PurchaseOrder,
        new_status: str,
        data: list[dict],
    ) -> None:
        """Update stock/inventory when PO status changes.

        For PURCHASE (ORDERED):
        - ProductVariantWarehouse.incoming_qty += ordered_qty
        - ProductVariant.total_incoming_qty += ordered_qty

        For INBOUND (DELIVERED) - first time (already_processed == 0):
        - ProductVariantWarehouse.incoming_qty -= remaining_qty (ordered_qty - received_qty)
        - ProductVariantWarehouse.physical_qty += received_qty
        - ProductVariant.total_incoming_qty -= remaining_qty
        - ProductVariant.total_available_qty += received_qty

        For INBOUND (DELIVERED) - subsequent (already_processed > 0):
        - ProductVariantWarehouse.physical_qty += qty_diff
        - ProductVariant.total_available_qty += qty_diff

        Args:
            po: PurchaseOrder instance (must have warehouse.id attribute)
            new_status: Target status (ORDERED or DELIVERED)
            data: List of dicts with product_variant_id, ordered_qty, received_qty, updated_qty
        """
        if not data:
            return

        product_variant_ids = [item.get("product_variant_id") for item in data]
        product_variants = list(
            ProductVariant.objects.select_for_update()
            .only("id", "total_incoming_qty", "total_available_qty", "company_id", "udate")
            .filter(id__in=product_variant_ids)
        )
        product_variant_warehouses = list(
            ProductVariantWarehouse.objects.select_for_update()
            .only(
                "id",
                "incoming_qty",
                "physical_qty",
                "product_variant_id",
                "warehouse_id",
                "company_id",
                "udate",
            )
            .filter(product_variant__in=product_variants, warehouse_id=po.warehouse.id)
        )

        map_product_variant = {str(pv.id): pv for pv in product_variants}
        map_product_warehouse = {
            str(pvw.product_variant.id): pvw for pvw in product_variant_warehouses
        }

        new_pvws: list[ProductVariantWarehouse] = []
        update_pvws: list[ProductVariantWarehouse] = []
        update_pv: list[ProductVariant] = []
        update_fields_pvw = set()
        update_fields_pv = set()
        movements: list[dict] = []

        for item in data:
            product_variant_id = item.get("product_variant_id")
            ordered_qty = item.get("ordered_qty", 0)
            received_qty = item.get("received_qty", 0)
            already_processed = item.get("updated_qty", 0) or 0
            qty_diff = received_qty - already_processed

            pv = map_product_variant.get(str(product_variant_id))
            if not pv:
                raise ValidationError(f"ProductVariant with id {product_variant_id} not found")

            pvw = map_product_warehouse.get(str(product_variant_id))
            created_pvw = False
            if not pvw:
                pvw = ProductVariantWarehouse(
                    company=po.company,
                    product_variant=pv,
                    warehouse_id=po.warehouse.id,
                    incoming_qty=0,
                    physical_qty=0,
                )
                new_pvws.append(pvw)
                created_pvw = True
            else:
                update_pvws.append(pvw)

            if new_status == PurchaseOrder.POStatus.ORDERED:
                pvw.incoming_qty += ordered_qty
                update_fields_pvw.add("incoming_qty")

                pv.total_incoming_qty += ordered_qty
                update_fields_pv.add("total_incoming_qty")

                movements.append(
                    {
                        "product_variant_id": product_variant_id,
                        "qty": ordered_qty,
                        "field_change": "incoming_qty",
                        "qty_before": pvw.incoming_qty - ordered_qty,
                        "note": item.get("note"),
                    }
                )

            elif new_status == PurchaseOrder.POStatus.DELIVERED:
                if already_processed == 0:
                    pvw.incoming_qty = max(0, pvw.incoming_qty - received_qty)
                    pvw.physical_qty += received_qty
                    if not created_pvw:
                        update_fields_pvw.update(["incoming_qty", "physical_qty"])

                    if pv.total_incoming_qty is None or pv.total_incoming_qty == 0:
                        pv.total_incoming_qty = max(0, pvw.incoming_qty)
                    else:
                        pv.total_incoming_qty = max(0, pv.total_incoming_qty - received_qty)
                    pv.total_available_qty += received_qty
                    update_fields_pv.update(["total_incoming_qty", "total_available_qty"])

                    movements.append(
                        {
                            "product_variant_id": product_variant_id,
                            "qty": ordered_qty,
                            "field_change": "incoming_qty",
                            "qty_before": pvw.incoming_qty + ordered_qty,
                            "note": item.get("note"),
                        }
                    )
                    movements.append(
                        {
                            "product_variant_id": product_variant_id,
                            "qty": received_qty,
                            "field_change": "physical_qty",
                            "qty_before": pvw.physical_qty - received_qty,
                            "note": item.get("note"),
                        }
                    )
                else:
                    if qty_diff != 0:
                        pvw.physical_qty += qty_diff
                        update_fields_pvw.add("physical_qty")

                        pv.total_available_qty += qty_diff
                        update_fields_pv.add("total_available_qty")

                        movements.append(
                            {
                                "product_variant_id": product_variant_id,
                                "qty": qty_diff,
                                "field_change": "physical_qty",
                                "qty_before": pvw.physical_qty - qty_diff,
                                "note": item.get("note"),
                            }
                        )

                        incoming_adjustment = already_processed - received_qty
                        if incoming_adjustment != 0:
                            pvw.incoming_qty += incoming_adjustment
                            update_fields_pvw.add("incoming_qty")

                            pv.total_incoming_qty += incoming_adjustment
                            update_fields_pv.add("total_incoming_qty")

            elif new_status == PurchaseOrder.POStatus.COMPLETED:
                remaining_qty = (item.get("ordered_qty") or 0) - (item.get("received_qty") or 0)
                if remaining_qty > 0 and pvw.incoming_qty > 0:
                    incoming_qty_before = pvw.incoming_qty
                    pvw.incoming_qty = max(0, pvw.incoming_qty - remaining_qty)
                    update_fields_pvw.add("incoming_qty")

                    pv.total_incoming_qty = max(0, pv.total_incoming_qty - remaining_qty)
                    update_fields_pv.add("total_incoming_qty")

                    movements.append(
                        {
                            "product_variant_id": product_variant_id,
                            "qty": remaining_qty,
                            "field_change": "incoming_qty",
                            "qty_before": incoming_qty_before,
                            "note": item.get("note"),
                        }
                    )

            pvw.udate = timezone.now()
            pv.udate = timezone.now()
            update_fields_pvw.add("udate")
            update_fields_pv.add("udate")
            update_pv.append(pv)

        if movements:
            movement_type = (
                StockMovement.MovementType.PURCHASE
                if new_status == PurchaseOrder.POStatus.ORDERED
                else StockMovement.MovementType.INBOUND
            )
            self.record_multiple_stock_movements(
                warehouse_id=po.warehouse.id,
                company_id=po.company.id,
                data=movements,
                map_product_variant=map_product_variant,
                reference_number=po.purchase_order_number,
                movement_type=movement_type,
            )

        ProductVariantWarehouse.objects.bulk_create(new_pvws, batch_size=100)
        if update_pvws:
            ProductVariantWarehouse.objects.bulk_update(
                update_pvws, fields=list(update_fields_pvw), batch_size=100
            )
        if update_pv:
            ProductVariant.objects.bulk_update(
                update_pv, fields=list(update_fields_pv), batch_size=100
            )

    @transaction.atomic
    def adjust_stock_batch(self, updates: list[dict]) -> dict:
        results: list[dict] = []
        errors: list[dict] = []
        pvws_to_update: list[ProductVariantWarehouse] = []
        movements: list[dict] = []

        if not updates:
            return {
                "results": results,
                "errors": errors,
                "summary": {"total": 0, "successful": 0, "failed": 0},
            }

        warehouse_id = updates[0]["warehouse_id"]
        if any(str(u.get("warehouse_id")) != str(warehouse_id) for u in updates):
            raise ValidationError("All items in a batch adjustment must target the same warehouse.")
        variant_ids = [u.get("variant_id") for u in updates]

        pvws = list(
            ProductVariantWarehouse.objects.select_for_update()
            .filter(
                product_variant_id__in=variant_ids,
                warehouse_id=warehouse_id,
            )
            .select_related("product_variant", "company", "warehouse")
        )
        pvw_map = {str(pvw.product_variant.id): pvw for pvw in pvws}

        for update in updates:
            variant_id = update.get("variant_id")
            qty = int(update.get("qty", 0))
            update_type = update.get("type", "replace")

            pvw = pvw_map.get(str(variant_id))
            if not pvw:
                errors.append(
                    {
                        "variant_id": variant_id,
                        "warehouse_id": warehouse_id,
                        "error": "Stock record not found. Create a warehouse stock entry first.",
                    }
                )
                continue

            balance_before = pvw.physical_qty

            if update_type in ("replace", "set"):
                new_qty = qty
                if new_qty < 0:
                    errors.append(
                        {
                            "variant_id": variant_id,
                            "warehouse_id": warehouse_id,
                            "error": f"Result would be negative stock ({new_qty}). Operation rejected.",
                        }
                    )
                    continue
            elif update_type == "add":
                new_qty = pvw.physical_qty + qty
                if new_qty < 0:
                    errors.append(
                        {
                            "variant_id": variant_id,
                            "warehouse_id": warehouse_id,
                            "error": f"Result would be negative stock ({new_qty}). Operation rejected.",
                        }
                    )
                    continue
            elif update_type == "min":
                new_qty = pvw.physical_qty - qty
                if new_qty < 0:
                    errors.append(
                        {
                            "variant_id": variant_id,
                            "warehouse_id": warehouse_id,
                            "error": f"Insufficient stock. Current: {pvw.physical_qty}, requested reduction: {qty}",
                        }
                    )
                    continue
            else:
                errors.append(
                    {
                        "variant_id": variant_id,
                        "warehouse_id": warehouse_id,
                        "error": f"Invalid type: {update_type}",
                    }
                )
                continue

            pvw.physical_qty = new_qty
            pvws_to_update.append(pvw)

            movements.append(
                {
                    "product_variant_id": variant_id,
                    "qty": new_qty - balance_before,
                    "field_change": "physical_qty",
                    "qty_before": balance_before,
                    "note": f"Bulk {update_type}: {qty}",
                }
            )

            results.append(
                {
                    "variant_id": variant_id,
                    "warehouse_id": warehouse_id,
                    "old_qty": balance_before,
                    "new_qty": new_qty,
                }
            )

        if pvws_to_update:
            ProductVariantWarehouse.objects.bulk_update(
                pvws_to_update, ["physical_qty"], batch_size=100
            )

            variant_ids_str = [str(pvw.product_variant.id) for pvw in pvws_to_update]
            variants = list(
                ProductVariant.objects.select_for_update()
                .filter(id__in=variant_ids_str)
                .prefetch_related("warehouse_stocks")
            )

            for variant in variants:
                variant.total_available_qty = sum(
                    w.physical_qty - w.checkout_qty for w in variant.warehouse_stocks.all()
                )

            ProductVariant.objects.bulk_update(variants, ["total_available_qty"], batch_size=100)

            map_product_variant = {str(v.id): v for v in variants}

            if movements:
                company_id = pvws_to_update[0].company.id
                self.record_multiple_stock_movements(
                    warehouse_id=warehouse_id,
                    company_id=company_id,
                    data=movements,
                    map_product_variant=map_product_variant,
                    reference_number="BULK_ADJ",
                    movement_type=StockMovement.MovementType.ADJUSTMENT,
                )

        return {
            "results": results,
            "errors": errors,
            "summary": {"total": len(updates), "successful": len(results), "failed": len(errors)},
        }

    @transaction.atomic
    def update_cogs_on_po(
        self,
        po: PurchaseOrder,
        new_status: str,
        data: list[dict],
    ) -> None:
        """Update COGS when PO status changes.

        For INBOUND (DELIVERED) - first time (already_processed == 0):
        - Create COGS record

        For INBOUND (DELIVERED) - subsequent (already_processed > 0):
        - Update COGS record if exists

        Args:
            po: PurchaseOrder instance (must have warehouse.id attribute)
            new_status: Target status (ORDERED or DELIVERED)
            data: List of dicts with product_variant_id, ordered_qty, received_qty, updated_qty
        """
        if not data:
            return

        if new_status != PurchaseOrder.POStatus.DELIVERED:
            return

        product_variant_ids = [item.get("product_variant_id") for item in data]
        product_variants = list(
            ProductVariant.objects.filter(id__in=product_variant_ids)
            .select_related("product")
            .only(
                "id",
                "product_id",
                "company_id",
                "product__length",
                "product__width",
                "product__height",
            )
        )

        map_product_variant = {str(pv.id): pv for pv in product_variants}

        reference_number: str | None = getattr(po, "purchase_order_number", None)
        existing_cogs_map: dict = {}
        if reference_number:
            existing_cogs_map = {
                str(cogs.product_variant.id): cogs
                for cogs in ProductCogs.objects.select_for_update()
                .only(
                    "id",
                    "product_variant_id",
                    "warehouse_id",
                    "company_id",
                    "original_qty",
                    "remaining_qty",
                    "price_rmb",
                    "exchange_rate",
                    "allocated_shipping_fee",
                    "allocated_delivery_fee",
                    "allocated_commission_fee",
                    "cogs_amount",
                )
                .filter(
                    product_variant_id__in=product_variant_ids,
                    warehouse=po.warehouse,
                    reference_number=reference_number,
                )
            }

        shipping_fee = getattr(po, "shipping_fee", 0) or 0
        delivery_fee = Decimal(str(getattr(po, "delivery_fee", 0) or 0))
        exchange_rate = Decimal(str(getattr(po, "exchange_rate", 1) or 1))

        # Step 1: compute CBM per item (for shipping allocation)
        item_volumes: dict = {}
        total_volume = Decimal("0")
        for item in data:
            pv = map_product_variant.get(str(item.get("product_variant_id")))
            if pv and pv.product:
                length = Decimal(str(pv.product.length or 0))
                width = Decimal(str(pv.product.width or 0))
                height = Decimal(str(pv.product.height or 0))
                ordered_qty = Decimal(str(item.get("ordered_qty", 0) or 0))
                cbm_per_unit = length * width * height / Decimal("1000000")
                item_volumes[str(item.get("product_variant_id"))] = {
                    "cbm": cbm_per_unit,
                    "qty": ordered_qty,
                    "total_cbm": cbm_per_unit * ordered_qty,
                    "item_value_idr": Decimal(str(item.get("discounted_total_price_base", 0) or 0)),
                }
                total_volume += cbm_per_unit * ordered_qty

        # Step 2: compute value totals (for delivery + commission allocation)
        total_item_amount_idr = Decimal(str(getattr(po, "total_item_amount", 0) or 0))
        commission_fee_total = Decimal(str(getattr(po, "commission_fee", 0) or 0))
        total_delivery_fee_idr = delivery_fee * exchange_rate

        # Step 3: allocate each fee per item
        for item_id, item_data in item_volumes.items():
            # Shipping: CBM-proportional
            if total_volume > 0:
                cbm_ratio = item_data["total_cbm"] / total_volume
                item_data["shipping_share"] = int(round(shipping_fee * cbm_ratio))
            else:
                item_data["shipping_share"] = 0

            # Delivery fee and commission: value-proportional
            if total_item_amount_idr > 0:
                value_ratio = item_data["item_value_idr"] / total_item_amount_idr
                item_data["delivery_share"] = int(round(total_delivery_fee_idr * value_ratio))
                item_data["commission_share"] = int(round(commission_fee_total * value_ratio))
            else:
                item_data["delivery_share"] = 0
                item_data["commission_share"] = 0

        create_cogs_records: list[ProductCogs] = []
        update_cogs_records: list[ProductCogs] = []

        for item in data:
            product_variant_id = item.get("product_variant_id")
            received_qty = item.get("received_qty", 0)
            already_processed = item.get("updated_qty", 0) or 0
            qty_diff = received_qty - already_processed

            pv = map_product_variant.get(str(product_variant_id))
            if not pv:
                raise ValidationError(f"ProductVariant with id {product_variant_id} not found")

            if already_processed == 0:
                if received_qty > 0:
                    unit_price_foreign = (
                        item.get("discounted_unit_price_foreign")
                        or item.get("unit_price_foreign")
                        or 0
                    )
                    item_exchange_rate = item.get("exchange_rate") or int(exchange_rate)
                    invoice_date = getattr(po, "invoice_date", None) or timezone.now().date()

                    allocated_shipping = 0
                    allocated_delivery = 0
                    allocated_commission = 0
                    if str(product_variant_id) in item_volumes:
                        vol_data = item_volumes[str(product_variant_id)]
                        allocated_shipping = vol_data.get("shipping_share", 0)
                        allocated_delivery = vol_data.get("delivery_share", 0)
                        allocated_commission = vol_data.get("commission_share", 0)

                    unit_price_idr = Decimal("0")
                    if unit_price_foreign and item_exchange_rate and item_exchange_rate != 1:
                        price_rmb = unit_price_foreign
                        unit_price_idr = Decimal(str(unit_price_foreign)) * Decimal(
                            str(item_exchange_rate)
                        )
                    else:
                        price_rmb = Decimal("0")
                        unit_price_idr = Decimal(str(item.get("unit_price_base") or 0))

                    shipping_per_unit = (
                        Decimal(str(allocated_shipping)) / Decimal(str(received_qty))
                        if received_qty > 0
                        else Decimal("0")
                    )
                    delivery_per_unit = (
                        Decimal(str(allocated_delivery)) / Decimal(str(received_qty))
                        if received_qty > 0
                        else Decimal("0")
                    )
                    commission_per_unit = (
                        Decimal(str(allocated_commission)) / Decimal(str(received_qty))
                        if received_qty > 0
                        else Decimal("0")
                    )
                    cogs_amount = int(
                        unit_price_idr + shipping_per_unit + delivery_per_unit + commission_per_unit
                    )

                    assert reference_number is not None
                    create_cogs_records.append(
                        ProductCogs(
                            company=po.company,
                            product_variant=pv,
                            warehouse=po.warehouse,
                            reference_number=reference_number,
                            purchase_date=invoice_date,
                            price_rmb=price_rmb,
                            exchange_rate=item_exchange_rate,
                            cogs_amount=cogs_amount,
                            allocated_shipping_fee=allocated_shipping,
                            allocated_delivery_fee=allocated_delivery,
                            allocated_commission_fee=allocated_commission,
                            original_qty=received_qty,
                            remaining_qty=received_qty,
                        )
                    )
            else:
                existing_cogs = existing_cogs_map.get(product_variant_id)
                if existing_cogs and qty_diff != 0:
                    if qty_diff < 0 and existing_cogs.remaining_qty + qty_diff < 0:
                        raise ValidationError(
                            f"Cannot reduce received qty for {pv}. "
                            f"{abs(qty_diff) - existing_cogs.remaining_qty} units already sold from this COGS layer."
                        )
                    existing_cogs.original_qty = received_qty
                    existing_cogs.remaining_qty += qty_diff
                    unit_price_idr = Decimal(str(existing_cogs.price_rmb)) * Decimal(
                        str(existing_cogs.exchange_rate)
                    )
                    shipping_per_unit = (
                        Decimal(str(existing_cogs.allocated_shipping_fee))
                        / Decimal(str(received_qty))
                        if received_qty > 0
                        else Decimal("0")
                    )
                    delivery_per_unit = (
                        Decimal(str(existing_cogs.allocated_delivery_fee))
                        / Decimal(str(received_qty))
                        if received_qty > 0
                        else Decimal("0")
                    )
                    commission_per_unit = (
                        Decimal(str(existing_cogs.allocated_commission_fee))
                        / Decimal(str(received_qty))
                        if received_qty > 0
                        else Decimal("0")
                    )
                    existing_cogs.cogs_amount = int(
                        unit_price_idr + shipping_per_unit + delivery_per_unit + commission_per_unit
                    )
                    update_cogs_records.append(existing_cogs)

        if create_cogs_records:
            ProductCogs.objects.bulk_create(create_cogs_records, batch_size=100)

        if update_cogs_records:
            ProductCogs.objects.bulk_update(
                update_cogs_records,
                fields=["original_qty", "remaining_qty", "cogs_amount", "allocated_commission_fee"],
                batch_size=100,
            )

    @staticmethod
    def parse_marketplace_xlsx(file_obj: Any) -> list[dict]:
        """
        Parse an xlsx file and return list of {"sku": str, "qty": int}.
        Detects header row in first 5 rows.
        SKU column names (case-insensitive): sku reference no., seller sku, sku, sku_variant_code
        Stock column names (case-insensitive): current stock, normal stock, available stock, stock, quantity, qty
        Skips rows where SKU cell is empty or None.
        Returns [] if no valid SKU/stock columns found -- caller should handle this.
        """
        import io
        import re
        import zipfile

        import openpyxl

        raw = file_obj.read()
        buf = io.BytesIO(raw)
        patched = io.BytesIO()
        with zipfile.ZipFile(buf, "r") as zin, zipfile.ZipFile(patched, "w") as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename.startswith("xl/worksheets/") and info.filename.endswith(".xml"):
                    data = re.sub(rb' activePane="[^"]*"', b"", data)
                zout.writestr(info, data)
        patched.seek(0)
        wb = openpyxl.load_workbook(patched, data_only=True)
        ws = wb.active

        SKU_ALIASES = {"sku reference no.", "seller sku", "sku", "sku_variant_code"}
        STOCK_ALIASES = {
            "current stock",
            "normal stock",
            "available stock",
            "stock",
            "quantity",
            "qty",
            "stok",
        }
        PRODUCT_NAME_ALIASES = {"nama produk", "product name", "product", "nama"}
        VARIANT_NAME_ALIASES = {"nama variasi", "variation name", "variasi", "variant name"}

        rows = list(ws.iter_rows(values_only=True))

        header_row_idx = None
        sku_col_idx = None
        stock_col_idx = None
        product_name_col_idx = None
        variant_name_col_idx = None
        for i, row in enumerate(rows[:5]):
            for j, cell in enumerate(row):
                if cell is None:
                    continue
                cell_lower = str(cell).strip().lower()
                if cell_lower in SKU_ALIASES and sku_col_idx is None:
                    sku_col_idx = j
                if cell_lower in STOCK_ALIASES and stock_col_idx is None:
                    stock_col_idx = j
                if cell_lower in PRODUCT_NAME_ALIASES and product_name_col_idx is None:
                    product_name_col_idx = j
                if cell_lower in VARIANT_NAME_ALIASES and variant_name_col_idx is None:
                    variant_name_col_idx = j
            if sku_col_idx is not None and stock_col_idx is not None:
                header_row_idx = i
                break

        if header_row_idx is None or sku_col_idx is None or stock_col_idx is None:
            return []

        result = []
        for row in rows[header_row_idx + 1 :]:
            if not row or len(row) <= max(sku_col_idx, stock_col_idx):
                continue
            sku = row[sku_col_idx]
            qty_raw = row[stock_col_idx]
            if sku is None or str(sku).strip() == "":
                continue
            try:
                qty = int(float(str(qty_raw))) if qty_raw is not None else 0
            except (ValueError, TypeError):
                continue
            file_product_name = (
                str(row[product_name_col_idx]).strip()
                if product_name_col_idx is not None
                and product_name_col_idx < len(row)
                and row[product_name_col_idx] is not None
                else ""
            )
            file_variant_name = (
                str(row[variant_name_col_idx]).strip()
                if variant_name_col_idx is not None
                and variant_name_col_idx < len(row)
                and row[variant_name_col_idx] is not None
                else ""
            )
            result.append(
                {
                    "sku": str(sku).strip(),
                    "qty": qty,
                    "file_product_name": file_product_name,
                    "file_variant_name": file_variant_name,
                }
            )

        wb.close()
        return result

    @transaction.atomic
    def reconcile_marketplace_stock(
        self,
        rows: list[dict],
        warehouse_id: str,
        company_id: str,
        marketplace_name: str,
        dry_run: bool = False,
    ) -> dict:
        """
        Reconcile stock from a marketplace file.
        rows: list of {"sku": str, "qty": int}
        For each row:
          - Find ProductVariant by sku_variant_code (company-scoped)
          - If not found: add to not_found list
          - If found: get or create ProductVariantWarehouse for the warehouse
          - If current physical_qty == target qty: add to skipped
          - If different: compute delta = target - current; update physical_qty; create MARKETPLACE_SYNC movement
        dry_run=True: compute all results but do NOT save anything.
        """
        from apps.inventory.models import ProductVariant, ProductVariantWarehouse, StockMovement

        reconciled = []
        skipped = []
        not_found = []
        errors = []

        if not rows:
            return {
                "reconciled": reconciled,
                "skipped": skipped,
                "not_found": not_found,
                "errors": errors,
                "summary": {"total": 0, "reconciled": 0, "skipped": 0, "not_found": 0},
            }

        skus = [r["sku"] for r in rows]
        variants_qs = ProductVariant.objects.filter(
            sku_variant_code__in=skus,
            company_id=company_id,
            is_active=True,
        ).select_related("company", "product")
        variant_map: dict[str, ProductVariant] = {v.sku_variant_code: v for v in variants_qs}

        variant_ids = [v.id for v in variants_qs]
        pvws_qs = ProductVariantWarehouse.objects.select_for_update().filter(
            product_variant_id__in=variant_ids,
            warehouse_id=warehouse_id,
        )
        pvw_map: dict[str, ProductVariantWarehouse] = {
            str(pvw.product_variant_id): pvw  # type: ignore[attr-defined]
            for pvw in pvws_qs
        }

        movements_data: list[dict] = []
        pvws_to_update: list[ProductVariantWarehouse] = []
        pvws_to_create: list[ProductVariantWarehouse] = []

        reference_number = f"MKT-RECONCILE-{marketplace_name[:20].upper().replace(' ', '-')}"

        for row in rows:
            sku = row["sku"]
            target_qty = row["qty"]
            variant = variant_map.get(sku)

            if not variant:
                not_found.append(
                    {
                        "sku": sku,
                        "file_product_name": row.get("file_product_name", ""),
                        "file_variant_name": row.get("file_variant_name", ""),
                    }
                )
                continue

            variant_id_str = str(variant.id)
            pvw = pvw_map.get(variant_id_str)

            if pvw is None:
                pvw = ProductVariantWarehouse(
                    product_variant=variant,
                    warehouse_id=warehouse_id,
                    company_id=company_id,
                    physical_qty=0,
                    checkout_qty=0,
                )
                pvw_map[variant_id_str] = pvw
                pvws_to_create.append(pvw)

            current_qty = pvw.physical_qty
            if current_qty == target_qty:
                skipped.append(
                    {
                        "sku": sku,
                        "qty": target_qty,
                        "product_name": variant.product.name,
                        "variant_name": variant.name,
                    }
                )
                continue

            delta = target_qty - current_qty
            pvw.physical_qty = target_qty
            pvws_to_update.append(pvw)

            movements_data.append(
                {
                    "product_variant_id": str(variant.id),
                    "qty": delta,
                    "field_change": "physical_qty",
                    "qty_before": current_qty,
                    "note": f"Marketplace reconciliation: {marketplace_name}",
                }
            )
            reconciled.append(
                {
                    "sku": sku,
                    "variant_id": str(variant.id),
                    "before": current_qty,
                    "after": target_qty,
                    "delta": delta,
                    "product_name": variant.product.name,
                    "variant_name": variant.name,
                }
            )

        if not dry_run:
            if pvws_to_create:
                ProductVariantWarehouse.objects.bulk_create(pvws_to_create, ignore_conflicts=False)

            if pvws_to_update:
                changed_pvws = [p for p in pvws_to_update if p.pk]
                if changed_pvws:
                    ProductVariantWarehouse.objects.bulk_update(
                        changed_pvws, ["physical_qty"], batch_size=100
                    )

            all_changed_pvws = pvws_to_create + pvws_to_update
            if all_changed_pvws:
                changed_variant_ids = [str(pvw.product_variant_id) for pvw in all_changed_pvws]  # type: ignore[attr-defined]
                changed_variants = list(
                    ProductVariant.objects.filter(id__in=changed_variant_ids).prefetch_related(
                        "warehouse_stocks"
                    )
                )
                for v in changed_variants:
                    v.total_available_qty = sum(
                        w.physical_qty - w.checkout_qty for w in v.warehouse_stocks.all()
                    )
                ProductVariant.objects.bulk_update(
                    changed_variants, ["total_available_qty"], batch_size=100
                )

            if movements_data:
                stock_movements = []
                for item in movements_data:
                    pv_id = item["product_variant_id"]
                    delta = item["qty"]
                    qty_before = item["qty_before"]
                    stock_movements.append(
                        StockMovement(
                            company_id=company_id,
                            product_variant_id=pv_id,
                            warehouse_id=warehouse_id,
                            quantity=delta,
                            field_change=item["field_change"],
                            movement_type=StockMovement.MovementType.MARKETPLACE_SYNC,
                            balance_before=qty_before,
                            balance_after=qty_before + delta,
                            reference_number=reference_number,
                            note=item["note"],
                        )
                    )
                StockMovement.objects.bulk_create(stock_movements, batch_size=100)

        summary = {
            "total": len(rows),
            "reconciled": len(reconciled),
            "skipped": len(skipped),
            "not_found": len(not_found),
        }

        return {
            "reconciled": reconciled,
            "skipped": skipped,
            "not_found": not_found,
            "errors": errors,
            "summary": summary,
            "dry_run": dry_run,
        }
