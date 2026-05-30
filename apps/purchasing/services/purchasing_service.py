import logging
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django_ulid.models import ULIDField

from apps.inventory.models import ProductCogs, ProductVariantWarehouse, Warehouse
from apps.inventory.services.inventory_service import InventoryService
from apps.purchasing.models import PurchaseOrder, PurchaseOrderDetail
from core.models import Company

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)


class PurchaseOrderService:
    """
    Service for Purchase Order operations.
    Handles creation, updates, and inventory-related logic.
    """

    def _trigger_shopee_sync_batch(self, variant_ids: list[str], company_id: str) -> None:
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
            if not connection.shopee_shop:
                continue
            try:
                service.sync_batch(variant_ids, connection.shopee_shop)
            except Exception:
                logger.warning(
                    "Shopee sync_batch trigger failed for shop %s",
                    connection.shopee_shop.shop_id,
                    exc_info=True,
                )

    @transaction.atomic
    def create_purchase_order(self, data: dict) -> PurchaseOrder:
        """Create a Purchase Order with nested order details."""
        details_data = data.pop("order_details", [])
        warehouse_id = data.pop("warehouse_id")
        company_id = data.pop("company_id")

        warehouse = Warehouse.objects.get(id=warehouse_id)
        company = Company.objects.get(id=company_id)

        data.setdefault("status", PurchaseOrder.POStatus.DRAFT)
        po = PurchaseOrder.objects.create(warehouse=warehouse, company=company, **data)

        if details_data:
            order_details = []
            for detail_data in details_data:
                product_variant_id = detail_data.pop("product_variant_id")
                order_details.append(
                    PurchaseOrderDetail(
                        purchase_order=po,
                        product_variant_id=product_variant_id,
                        company=company,
                        **detail_data,
                    )
                )

            PurchaseOrderDetail.objects.bulk_create(order_details, batch_size=100)

        return po

    def check_purchase_order_requirements(
        self,
        po: "PurchaseOrder",
        new_status: str,
        incoming_data: dict | None = None,
    ) -> list[dict[str, str]]:
        """
        Returns all unmet field requirements for a PO status transition.
        Does NOT raise — returns empty list if all requirements are met.

        incoming_data: fields being submitted in the current request (update path).
                       Pass None when only checking the PO's existing state (advance_status path).

        Returns list of {"field", "label", "section", "message"}.
        """
        data = incoming_data or {}
        missing: list[dict[str, str]] = []

        zero_valid_fields = {"exchange_rate", "commission_fee_pct", "delivery_fee"}

        def _present(field: str) -> bool:
            """True if the field has a usable value in incoming_data or on the PO."""
            incoming_val = data.get(field)
            if incoming_val is not None:
                # File fields: treat empty string as absent
                if field in (
                    "purchase_order_invoice_file",
                    "delivery_order_file",
                    "delivery_order_invoice_file",
                ):
                    return bool(incoming_val)
                return True
            po_val = getattr(po, field, None)
            if field in zero_valid_fields:
                return po_val is not None
            return bool(po_val)

        if new_status == PurchaseOrder.POStatus.ORDERED:
            for field, label, section, message in [
                ("exchange_rate",               "Exchange Rate",       "Financial Setup",   "Exchange rate is required when moving to ORDERED."),
                ("purchase_order_invoice_file", "PO Invoice File",    "Attachments",       "PO invoice file is required when moving to ORDERED."),
                ("invoice_number",              "Invoice Number",      "Logistics & Dates", "Invoice number is required when moving to ORDERED."),
                ("invoice_date",                "Invoice Date",        "Logistics & Dates", "Invoice date is required when moving to ORDERED."),
                ("commission_fee_pct",          "Commission %",        "Financial Setup",   "Commission % is required when moving to ORDERED."),
                ("forwarder_name",              "Forwarder",           "General",           "Forwarder name is required when moving to ORDERED."),
                ("supplier_name",               "Supplier",            "General",           "Supplier name is required when moving to ORDERED."),
                ("shop_services",               "Jasa Belanja",        "General",           "Jasa belanja is required when moving to ORDERED."),
                ("delivery_fee",               "Delivery Fee (RMB)",  "Financial Setup",   "Delivery fee is required when moving to ORDERED. Can be 0."),
            ]:
                if not _present(field):
                    missing.append({"field": field, "label": label, "section": section, "message": message})

            # Order details: need at least one existing or incoming
            has_incoming_details = bool(data.get("order_details"))
            has_existing_details = (
                po.order_details.exists() if hasattr(po, "order_details") else False
            )
            if not has_incoming_details and not has_existing_details:
                missing.append({
                    "field": "order_details",
                    "label": "Order Items",
                    "section": "Order Items",
                    "message": "At least one order item is required when moving to ORDERED.",
                })

        elif new_status == PurchaseOrder.POStatus.SHIPPED:
            for field, label, section, message in [
                ("delivery_order_number", "Delivery Order No.",  "Logistics & Dates", "Delivery order number is required when moving to SHIPPED."),
                ("delivery_order_file",   "Delivery Order File", "Attachments",       "Delivery order file is required when moving to SHIPPED."),
                ("shipping_fee_per_cbm",  "Shipping Fee / CBM",  "Financial Setup",   "Shipping fee per CBM is required when moving to SHIPPED."),
                ("cbm",                   "CBM",                 "Logistics & Dates", "CBM is required when moving to SHIPPED."),
                ("weight",                "Weight (kg)",         "Logistics & Dates", "Weight is required when moving to SHIPPED."),
            ]:
                if not _present(field):
                    missing.append({"field": field, "label": label, "section": section, "message": message})

        elif new_status == PurchaseOrder.POStatus.DELIVERED:
            if not _present("delivery_order_invoice_file"):
                missing.append({
                    "field": "delivery_order_invoice_file",
                    "label": "DO Invoice File",
                    "section": "Attachments",
                    "message": "Delivery order invoice file is required when moving to DELIVERED.",
                })

        return missing

    def get_transition_warnings(
        self,
        po: "PurchaseOrder",
        new_status: str,
    ) -> list[dict[str, object]]:
        """
        Returns soft warnings for a status transition.
        Warnings do not block the transition — they ask the user to confirm.
        """
        warnings: list[dict[str, object]] = []

        if new_status == PurchaseOrder.POStatus.COMPLETED:
            partial_items = []
            for detail in po.order_details.select_related("product_variant").all():
                received = detail.received_qty or 0
                if received < detail.ordered_qty:
                    partial_items.append({
                        "name": detail.product_variant.name,
                        "ordered_qty": detail.ordered_qty,
                        "received_qty": received,
                    })
            if partial_items:
                warnings.append({
                    "type": "partial_receipt",
                    "message": f"{len(partial_items)} item(s) have received qty less than ordered qty.",
                    "items": partial_items,
                })

        return warnings

    @transaction.atomic
    def update_purchase_order(
        self, po: PurchaseOrder, data: dict, changed_by: "User | None" = None
    ) -> PurchaseOrder:
        """Update a Purchase Order and its details.

        Validations are handled by PurchaseOrderUpdateSerializer before this method is called.
        File compression is handled in the serializer's validate_<field> methods.
        """
        old_status = po.status
        new_status: str | None = data.get("status")

        # Enforce status transitions
        if new_status and new_status != old_status:
            allowed = PurchaseOrder.STATUS_TRANSITIONS.get(old_status, [])
            if new_status not in allowed and new_status != PurchaseOrder.POStatus.CANCELLED:
                raise ValidationError(
                    {
                        "status": f"Cannot transition from {old_status} to {new_status}. Allowed: {allowed}"
                    }
                )

        order_details = data.get("order_details", [])

        if new_status not in [PurchaseOrder.POStatus.CANCELLED]:
            for detail_data in order_details:
                ordered_qty = detail_data.get("ordered_qty")
                if ordered_qty is not None and ordered_qty < 0:
                    raise ValidationError(
                        {"order_details": f"ordered_qty cannot be negative. Value: {ordered_qty}"}
                    )
                received_qty = detail_data.get("received_qty")
                if received_qty is not None and received_qty < 0:
                    raise ValidationError(
                        {"order_details": f"received_qty cannot be negative. Value: {received_qty}"}
                    )

        existing_details_map = {str(d.id): d for d in po.order_details.all()}

        if old_status not in [PurchaseOrder.POStatus.DRAFT, PurchaseOrder.POStatus.ORDERED]:
            price_fields = {
                "unit_price_foreign",
                "discounted_unit_price_foreign",
                "unit_price_base",
                "discounted_unit_price_base",
                "total_price_foreign",
                "discounted_total_price_foreign",
                "total_price_base",
                "discounted_total_price_base",
            }
            for detail_data in order_details:
                detail_id = detail_data.get("id")
                if detail_id:
                    existing_detail = existing_details_map.get(detail_id)
                    if existing_detail:
                        for field in price_fields:
                            if detail_data.get(field) is not None:
                                existing_value = getattr(existing_detail, field, None)
                                if existing_value is not None and existing_value != detail_data.get(
                                    field
                                ):
                                    raise ValidationError(
                                        {
                                            "order_details": f"Cannot change {field} when status is {old_status}. Price fields can only be changed in DRAFT or ORDERED status."
                                        }
                                    )

        if new_status in [PurchaseOrder.POStatus.DELIVERED, PurchaseOrder.POStatus.COMPLETED]:
            existing_details_map = {str(d.id): d for d in po.order_details.all()}

            product_variant_ids = list(set(d.product_variant.id for d in po.order_details.all()))

            pvw_map = {
                str(pvw.product_variant.id): pvw
                for pvw in ProductVariantWarehouse.objects.filter(
                    warehouse=po.warehouse, product_variant_id__in=product_variant_ids
                )
            }

            cogs_map = {
                str(cogs.product_variant.id): cogs
                for cogs in ProductCogs.objects.filter(
                    warehouse=po.warehouse,
                    reference_number=po.purchase_order_number,
                    product_variant_id__in=product_variant_ids,
                )
            }

            for detail_data in order_details:
                detail_id = detail_data.get("id")
                if not detail_id:
                    continue
                existing_detail = existing_details_map.get(detail_id)
                if not existing_detail:
                    continue

                ordered_qty = detail_data.get("ordered_qty", existing_detail.ordered_qty)
                received_qty = detail_data.get("received_qty", existing_detail.received_qty or 0)
                existing_received_qty = existing_detail.received_qty or 0

                if received_qty < existing_received_qty:
                    qty_decrease = existing_received_qty - received_qty
                    pvw = pvw_map.get(str(existing_detail.product_variant.id))
                    if pvw:
                        if pvw.physical_qty < qty_decrease:
                            raise ValidationError(
                                {
                                    "order_details": f"Cannot decrease received_qty for {existing_detail.product_variant.name}. "
                                    f"Physical qty ({pvw.physical_qty}) is less than the decrease amount ({qty_decrease}). "
                                    f"There may be sales on this item."
                                }
                            )

                    cogs = cogs_map.get(str(existing_detail.product_variant.id))
                    if cogs:
                        if cogs.remaining_qty < qty_decrease:
                            raise ValidationError(
                                {
                                    "order_details": f"Cannot decrease received_qty for {existing_detail.product_variant.name}. "
                                    f"COGS remaining_qty ({cogs.remaining_qty}) is less than the decrease amount ({qty_decrease}). "
                                    f"There may be sales on this item."
                                }
                            )

                if received_qty > ordered_qty:
                    remarks = detail_data.get("remarks") or existing_detail.remarks
                    if not remarks:
                        raise ValidationError(
                            {
                                "order_details": f"Remarks is required for {existing_detail.product_variant.name} when received_qty ({received_qty}) exceeds ordered_qty ({ordered_qty})."
                            }
                        )

                if new_status == PurchaseOrder.POStatus.COMPLETED and received_qty < ordered_qty:
                    remarks = detail_data.get("remarks") or existing_detail.remarks
                    if not remarks:
                        raise ValidationError(
                            {
                                "order_details": f"Remarks is required for {existing_detail.product_variant.name} when moving to COMPLETED status with partial delivery (received_qty: {received_qty}, ordered_qty: {ordered_qty})."
                            }
                        )

        if new_status == PurchaseOrder.POStatus.DELIVERED and not data.get("delivery_date"):
            data["delivery_date"] = timezone.now()

        if new_status == PurchaseOrder.POStatus.CANCELLED:
            if old_status != PurchaseOrder.POStatus.DRAFT:
                raise ValidationError(
                    {
                        "status": f"Cannot cancel PO. Only PO in DRAFT status can be cancelled. Current status: {old_status}"
                    }
                )

        inventory_data = []
        if new_status == PurchaseOrder.POStatus.ORDERED:
            for detail in po.order_details.all():
                inventory_data.append(
                    {
                        "product_variant_id": detail.product_variant.id,
                        "ordered_qty": detail.ordered_qty,
                        "note": f"Stock movement for PO {po.purchase_order_number} purchase",
                    }
                )

            # Create AccountsPayable when PO moves to ORDERED
            from apps.finance.services.accounts_payable_service import AccountsPayableService

            AccountsPayableService().create_payable_from_po(po)

        elif new_status == PurchaseOrder.POStatus.DELIVERED:
            for item in order_details:
                detail_id = item.get("id")
                receive_date_str = item.get("received_date")
                received_qty = item.get("received_qty", 0)
                ordered_qty = item.get("ordered_qty", 0)
                updated_qty = item.get("updated_qty", 0) or 0
                product_variant_id = item.get("product_variant_id")
                if not product_variant_id:
                    continue
                if not receive_date_str:
                    continue

                qty_is_zero = received_qty == 0

                receive_date = datetime.fromisoformat(receive_date_str.replace("Z", "+00:00"))
                if receive_date and po.delivery_date and receive_date.date() < po.delivery_date:
                    continue

                if qty_is_zero:
                    continue

                existing_detail_obj = existing_details_map.get(detail_id) if detail_id else None
                discounted_total_price_base = 0
                if existing_detail_obj:
                    discounted_total_price_base = existing_detail_obj.discounted_total_price_base or 0

                inventory_data.append(
                    {
                        "product_variant_id": product_variant_id,
                        "ordered_qty": ordered_qty,
                        "received_qty": received_qty,
                        "updated_qty": updated_qty,
                        "unit_price_base": item.get("unit_price_base"),
                        "unit_price_foreign": item.get("unit_price_foreign"),
                        "discounted_unit_price_foreign": item.get("discounted_unit_price_foreign"),
                        "discounted_total_price_base": discounted_total_price_base,
                        "exchange_rate": po.exchange_rate,
                        "note": f"Stock movement for PO {po.purchase_order_number} inbound",
                    }
                )

        elif new_status == PurchaseOrder.POStatus.COMPLETED:
            for detail in po.order_details.all():
                inventory_data.append(
                    {
                        "product_variant_id": detail.product_variant.id,
                        "ordered_qty": detail.ordered_qty,
                        "received_qty": detail.received_qty or 0,
                        "updated_qty": detail.received_qty or 0,
                        "note": f"Stock movement for PO {po.purchase_order_number} completed",
                    }
                )

        if inventory_data:
            inventory_service = InventoryService()
            status_for_inventory: str = new_status  # type: ignore[assignment]
            inventory_service.update_stock_on_po(
                po=po,
                new_status=status_for_inventory,
                data=inventory_data,
            )
            inventory_service.update_cogs_on_po(
                po=po,
                new_status=status_for_inventory,
                data=inventory_data,
            )

        if new_status == PurchaseOrder.POStatus.DELIVERED and inventory_data:
            variant_ids_to_sync = [str(item["product_variant_id"]) for item in inventory_data]
            _ids = variant_ids_to_sync
            _company_id = str(po.company.id)
            transaction.on_commit(lambda: self._trigger_shopee_sync_batch(_ids, _company_id))

        details_data = data.pop("order_details", None)

        original_received_qtys = {}
        if details_data is not None and old_status == PurchaseOrder.POStatus.DELIVERED:
            existing_details_map = {str(d.id): d for d in po.order_details.all()}
            for detail_data in details_data:
                if detail_id := detail_data.get("id"):
                    if existing_detail := existing_details_map.get(detail_id):
                        original_received_qtys[detail_id] = existing_detail.received_qty or 0

        if new_status and new_status != old_status:
            from apps.purchasing.models import PurchaseOrderStatusHistory
            PurchaseOrderStatusHistory.objects.create(
                purchase_order=po,
                company=po.company,
                from_status=old_status,
                to_status=new_status,
                changed_by=changed_by,
            )

        updated_fields = ["udate"]
        for attr, value in data.items():
            if attr not in ("id", "_purchase_order"):
                updated_fields.append(attr)
                setattr(po, attr, value)
        po.save(update_fields=updated_fields)

        if details_data is not None:
            self._update_order_details(po, details_data, old_status, new_status or old_status)

        self._recalculate_po_totals(po)

        incremental_order_details = order_details if order_details and new_status is None else None

        if (
            old_status == PurchaseOrder.POStatus.DELIVERED
            and new_status is None
            and incremental_order_details
        ):
            existing_details_map = {str(d.id): d for d in po.order_details.all()}

            inventory_data = []
            for item in incremental_order_details:
                receive_date_str = item.get("received_date")
                received_qty = item.get("received_qty", 0)
                ordered_qty = item.get("ordered_qty", 0)
                product_variant_id = item.get("product_variant_id")
                if not product_variant_id:
                    continue
                if not receive_date_str:
                    continue

                qty_is_zero = received_qty == 0
                if qty_is_zero:
                    continue

                detail_id = item.get("id")
                original_received_qty = original_received_qtys.get(detail_id, 0)
                new_received_qty = item.get("received_qty", 0)
                if new_received_qty > original_received_qty:
                    received_qty = new_received_qty - original_received_qty
                    updated_qty = 0
                else:
                    continue

                detail_id = item.get("id")
                existing_detail_obj = existing_details_map.get(detail_id) if detail_id else None
                discounted_total_price_base = 0
                if existing_detail_obj:
                    discounted_total_price_base = existing_detail_obj.discounted_total_price_base or 0

                inventory_data.append(
                    {
                        "product_variant_id": product_variant_id,
                        "ordered_qty": ordered_qty,
                        "received_qty": received_qty,
                        "updated_qty": updated_qty,
                        "unit_price_base": item.get("unit_price_base"),
                        "unit_price_foreign": item.get("unit_price_foreign"),
                        "discounted_unit_price_foreign": item.get("discounted_unit_price_foreign"),
                        "discounted_total_price_base": discounted_total_price_base,
                        "exchange_rate": po.exchange_rate,
                        "note": f"Stock movement for PO {po.purchase_order_number} inbound",
                    }
                )

            if inventory_data:
                inventory_service = InventoryService()
                inventory_service.update_stock_on_po(
                    po=po,
                    new_status=PurchaseOrder.POStatus.DELIVERED,
                    data=inventory_data,
                )
                inventory_service.update_cogs_on_po(
                    po=po,
                    new_status=PurchaseOrder.POStatus.DELIVERED,
                    data=inventory_data,
                )

                variant_ids_to_sync = [str(item["product_variant_id"]) for item in inventory_data]
                _ids2 = variant_ids_to_sync
                _company_id2 = str(po.company.id)
                transaction.on_commit(lambda: self._trigger_shopee_sync_batch(_ids2, _company_id2))

        return po

    def _update_order_details(
        self, po: PurchaseOrder, details_data: list, current_status: str, new_status: str
    ) -> None:
        """Handle order details update/create/delete."""
        ulid_field = ULIDField()

        new_details: list[PurchaseOrderDetail] = []
        update_details: list[PurchaseOrderDetail] = []
        update_fields_set: set = set()
        existing_detail_ids: list = []

        for detail_data in details_data:
            if detail_id := detail_data.get("id"):
                converted_id = ulid_field.to_python(detail_id)
                existing_detail_ids.append(converted_id)

        existing_details_map = {}
        if existing_detail_ids:
            existing_details_map = {
                d.id: d for d in po.order_details.filter(id__in=existing_detail_ids)
            }

        for detail_data in details_data:
            detail_id = detail_data.get("id")
            if detail_id:
                converted_id = ulid_field.to_python(detail_id)
                detail = existing_details_map.get(converted_id)
                if not detail:
                    if current_status == PurchaseOrder.POStatus.DRAFT:
                        pass
                    else:
                        raise ValidationError(f"Detail with id {detail_id} not found")
                else:
                    is_delivered = new_status == PurchaseOrder.POStatus.DELIVERED
                    for attr, value in detail_data.items():
                        if attr == "id":
                            continue
                        if is_delivered and attr in ("updated_qty", "received_qty"):
                            continue
                        setattr(detail, attr, value)
                        update_fields_set.add(attr)

                    if is_delivered:
                        detail.updated_qty = detail_data.get("received_qty", 0)
                        detail.received_qty = detail_data.get("received_qty", 0)
                        update_fields_set.update(["updated_qty", "received_qty"])

                    update_details.append(detail)
            else:
                if po.status == PurchaseOrder.POStatus.DRAFT:
                    product_variant_id = ulid_field.to_python(detail_data.get("product_variant_id"))
                    detail_data_copy = {
                        k: v for k, v in detail_data.items() if k != "product_variant_id"
                    }
                    detail = PurchaseOrderDetail(
                        purchase_order=po,
                        product_variant_id=product_variant_id,
                        company=po.company,
                        **detail_data_copy,
                    )
                    new_details.append(detail)

        if po.status == PurchaseOrder.POStatus.DRAFT:
            ids_to_keep = [d.id for d in update_details] + [d.id for d in new_details]
            po.order_details.exclude(id__in=ids_to_keep).delete()

        if update_details:
            update_fields_list = list(update_fields_set) + ["udate"]
            PurchaseOrderDetail.objects.bulk_update(
                update_details, update_fields_list, batch_size=100
            )

        if new_details:
            PurchaseOrderDetail.objects.bulk_create(new_details, batch_size=100)

    def _recalculate_po_totals(self, po: PurchaseOrder) -> None:
        """Recalculate PO totals based on order details and fee fields."""
        total_ordered_qty = 0
        total_received_qty = 0
        total_item_amount = 0
        total_item_rmb = Decimal("0")

        for detail in po.order_details.all():
            total_ordered_qty += detail.ordered_qty or 0
            total_received_qty += detail.received_qty or 0
            total_item_amount += detail.discounted_total_price_base or 0
            total_item_rmb += Decimal(str(detail.discounted_total_price_foreign or 0))

        exchange_rate = Decimal(str(po.exchange_rate or 0))
        commission_fee_pct = Decimal(str(po.commission_fee_pct or 0))
        shipping_fee_per_cbm = Decimal(str(po.shipping_fee_per_cbm or 0))
        cbm = Decimal(str(po.cbm or 0))

        commission_fee = int(round(commission_fee_pct / Decimal("100") * total_item_rmb * exchange_rate))
        shipping_fee = int(round(shipping_fee_per_cbm * cbm))
        procure_amount = shipping_fee + commission_fee
        total_order_amount = total_item_amount + commission_fee
        total_amount = total_item_amount + commission_fee + shipping_fee

        update_fields = []
        if po.total_ordered_qty != total_ordered_qty:
            po.total_ordered_qty = total_ordered_qty
            update_fields.append("total_ordered_qty")
        if po.total_received_qty != total_received_qty:
            po.total_received_qty = total_received_qty
            update_fields.append("total_received_qty")
        if po.total_item_amount != total_item_amount:
            po.total_item_amount = total_item_amount
            update_fields.append("total_item_amount")
        if po.commission_fee != commission_fee:
            po.commission_fee = commission_fee
            update_fields.append("commission_fee")
        if po.shipping_fee != shipping_fee:
            po.shipping_fee = shipping_fee
            update_fields.append("shipping_fee")
        if po.procure_amount != procure_amount:
            po.procure_amount = procure_amount
            update_fields.append("procure_amount")
        if po.total_order_amount != total_order_amount:
            po.total_order_amount = total_order_amount
            update_fields.append("total_order_amount")
        if po.total_amount != total_amount:
            po.total_amount = total_amount
            update_fields.append("total_amount")

        if update_fields:
            update_fields.append("udate")
            po.save(update_fields=update_fields)

        # Sync AP total_amount if it exists
        try:
            ap = po.payable
            if ap.total_amount != po.total_amount:
                ap.total_amount = po.total_amount
                ap.save(update_fields=["total_amount", "udate"])
        except Exception:
            pass  # AP may not exist yet (DRAFT status)
