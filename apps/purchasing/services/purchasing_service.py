import logging
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from django_ulid.models import ULIDField

from apps.inventory.models import ProductCogs, ProductVariant, ProductVariantWarehouse, Warehouse
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

    @transaction.atomic
    def add_draft_line(
        self,
        po: PurchaseOrder,
        sourcing_item_id: str,
        ordered_qty: int,
        unit_price_foreign: Decimal | None = None,
    ) -> PurchaseOrderDetail:
        """Add a sourcing pool item as a draft line to a DRAFT or ORDERED PO."""
        from apps.purchasing.models import SourcingPoolItem

        if po.status not in [PurchaseOrder.POStatus.DRAFT, PurchaseOrder.POStatus.ORDERED]:
            raise ValidationError(f"Cannot add lines to a PO with status {po.status}.")

        if ordered_qty <= 0:
            raise ValidationError("ordered_qty must be a positive integer.")

        sourcing_item = SourcingPoolItem.objects.select_for_update().get(
            id=sourcing_item_id, company=po.company
        )

        display_name = sourcing_item.product_name or "(Unnamed)"

        if po.order_details.filter(sourcing_item=sourcing_item).exists():
            raise ValidationError(
                f"'{display_name} / {sourcing_item.variant_name}' is already in this PO."
            )

        draft_product_name = f"{display_name} / {sourcing_item.variant_name}"
        effective_price = (
            unit_price_foreign if unit_price_foreign is not None else sourcing_item.unit_price
        )

        detail = PurchaseOrderDetail.objects.create(
            purchase_order=po,
            company=po.company,
            product_variant=None,
            sourcing_item=sourcing_item,
            draft_product_name=draft_product_name,
            ordered_qty=ordered_qty,
            unit_price_foreign=effective_price,
        )

        SourcingPoolItem.objects.filter(id=sourcing_item_id).update(
            times_ordered=F("times_ordered") + 1
        )

        self._recalculate_po_totals(po)
        return detail

    @transaction.atomic
    def finalize_draft_line(
        self,
        detail: PurchaseOrderDetail,
        sku_suffix: str,
        category_id: str | None = None,
        product_name: str | None = None,
        dim1_key: str | None = None,
        dim1_value: str | None = None,
        dim2_key: str | None = None,
        dim2_value: str | None = None,
    ) -> PurchaseOrderDetail:
        """Convert a draft sourcing line into a real Product+Variant and link it."""
        from django.db import IntegrityError

        from apps.inventory.models import ProductVariant as PV
        from apps.inventory.services.product_service import ProductService

        # Lock the row to prevent concurrent finalization of the same draft line.
        detail = PurchaseOrderDetail.objects.select_for_update().get(id=detail.id)

        if detail.product_variant_id is not None or detail.sourcing_item_id is None:  # type: ignore[attr-defined]
            raise ValidationError("Detail is not a draft sourcing line.")

        if not sku_suffix or not sku_suffix.strip():
            raise ValidationError("sku_suffix is required.")

        item = detail.sourcing_item
        final_product_name = (product_name or "").strip() or (item.product_name or "").strip()  # type: ignore[union-attr]
        if not final_product_name:
            raise ValidationError(
                "product_name is required to finalize this line — "
                "the pool item has no product name."
            )
        final_category_id = category_id or (str(item.category_id) if item.category_id else None)  # type: ignore[union-attr]

        if not final_category_id:
            raise ValidationError(
                "category_id is required to finalize this line. "
                "The pool item has no category — provide one explicitly."
            )

        dim1k = (dim1_key or "").strip()
        dim2k = (dim2_key or "").strip()
        dim1v = (dim1_value or "").strip()
        dim2v = (dim2_value or "").strip()
        variant_values: dict[str, str] = {}
        if dim1k and dim1v:
            variant_values[dim1k] = dim1v
        if dim2k and dim2v:
            variant_values[dim2k] = dim2v

        try:
            result = ProductService().create_product_with_variants(
                {
                    "company_id": str(detail.company_id),  # type: ignore[attr-defined]
                    "category_id": final_category_id,
                    "name": final_product_name,
                    "description": "",
                    "variant_options": [],
                    "specifications": {},
                    "weight": 0,
                    "length": 0,
                    "width": 0,
                    "height": 0,
                    "dim1_key": dim1k if dim1v else "",
                    "dim2_key": dim2k if dim2v else "",
                    "dim1_options": [dim1v] if dim1v else [],
                    "dim2_options": [dim2v] if dim2v else [],
                    "variants": [
                        {
                            "variant_values": variant_values,
                            "sku_variant_code": sku_suffix.strip(),
                            "base_price": 0,
                        }
                    ],
                }
            )
        except IntegrityError:
            raise ValidationError(
                f"SKU '{sku_suffix.strip()}' already exists. Choose a different sku_suffix."
            )

        variant_id = result[0]["variants"][0]["id"]
        variant = PV.objects.get(id=variant_id)

        detail.product_variant = variant
        detail.sourcing_item = None
        detail.draft_product_name = ""
        detail.save(
            update_fields=["product_variant", "sourcing_item", "draft_product_name", "udate"]
        )

        # Recompute IDR base prices for this line now that it has a real variant.
        po = detail.purchase_order
        if po.exchange_rate:
            self._recalculate_item_prices(po)

        return detail

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

    def _ensure_product_supplier_links(self, po: "PurchaseOrder", variant_ids: list[str]) -> None:
        """Silently create ProductSupplier records for products not yet linked to PO's supplier."""
        if not po.supplier or not variant_ids:
            return
        try:
            from apps.inventory.models import ProductSupplier

            raw_pairs = list(
                ProductVariant.objects.filter(id__in=variant_ids).values_list("id", "product_id")
            )
            product_ids = list({str(pid) for _, pid in raw_pairs})
            if not product_ids:
                return

            existing_product_ids = set(
                str(pid)
                for pid in ProductSupplier.objects.filter(
                    product_id__in=product_ids,
                    supplier=po.supplier,
                ).values_list("product_id", flat=True)
            )

            missing_ids = [pid for pid in product_ids if pid not in existing_product_ids]
            if missing_ids:
                ProductSupplier.objects.bulk_create(
                    [
                        ProductSupplier(
                            product_id=pid,
                            supplier=po.supplier,
                            company=po.company,
                        )
                        for pid in missing_ids
                    ],
                    ignore_conflicts=True,
                )
        except Exception:
            logger.exception("Failed to auto-link product-supplier for PO %s", po.id)

    @transaction.atomic
    def _snapshot_sales_metrics_at_ordered(self, po: PurchaseOrder) -> None:
        """Capture avg_sales (7d+30d), stock_on_hand, incoming_qty snapshots on each detail."""
        details = list(po.order_details.filter(product_variant__isnull=False))
        if not details:
            return

        variant_ids = [str(d.product_variant.id) for d in details]  # type: ignore[union-attr]

        # Avg sales
        avg7_list = InventoryService().get_avg_sales_per_day(variant_ids=variant_ids, days=7)
        avg30_list = InventoryService().get_avg_sales_per_day(variant_ids=variant_ids, days=30)
        avg7_map: dict[str, float] = {r["variant_id"]: r["avg_sales_per_day"] for r in avg7_list}
        avg30_map: dict[str, float] = {r["variant_id"]: r["avg_sales_per_day"] for r in avg30_list}

        # SOH — sum physical_qty across all warehouses
        soh_map: dict[str, int] = {}
        for pvw in ProductVariantWarehouse.objects.filter(
            product_variant_id__in=variant_ids
        ).values("product_variant_id", "physical_qty"):
            vid = str(pvw["product_variant_id"])
            soh_map[vid] = soh_map.get(vid, 0) + (pvw["physical_qty"] or 0)

        # Incoming — remaining qty from OTHER open POs (ORDERED+SHIPPED), excluding this PO
        incoming_map: dict[str, int] = {}
        for row in (
            PurchaseOrderDetail.objects.filter(
                purchase_order__status__in=[
                    PurchaseOrder.POStatus.ORDERED,
                    PurchaseOrder.POStatus.SHIPPED,
                ],
                product_variant_id__in=variant_ids,
            )
            .exclude(purchase_order=po)
            .values("product_variant_id", "ordered_qty", "received_qty")
        ):
            vid = str(row["product_variant_id"])
            gap = max(0, (row["ordered_qty"] or 0) - (row["received_qty"] or 0))
            incoming_map[vid] = incoming_map.get(vid, 0) + gap

        # Write snapshots
        for detail in details:
            vid = str(detail.product_variant.id)  # type: ignore[union-attr]
            detail.avg_sales = avg30_map.get(vid, 0.0)
            detail.avg_sales_7d = avg7_map.get(vid, 0.0)
            detail.stock_on_hand = soh_map.get(vid, 0)
            detail.incoming_qty = incoming_map.get(vid, 0)

        PurchaseOrderDetail.objects.bulk_update(
            details, ["avg_sales", "avg_sales_7d", "stock_on_hand", "incoming_qty"]
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
            variant_ids: list[str] = []
            for detail_data in details_data:
                product_variant_id = detail_data.pop("product_variant_id")
                variant_ids.append(str(product_variant_id))
                order_details.append(
                    PurchaseOrderDetail(
                        purchase_order=po,
                        product_variant_id=product_variant_id,
                        company=company,
                        **detail_data,
                    )
                )

            PurchaseOrderDetail.objects.bulk_create(order_details, batch_size=100)
            self._ensure_product_supplier_links(po, variant_ids)
            self._recalculate_forecast_cbm(po)

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
                (
                    "exchange_rate",
                    "Exchange Rate",
                    "Financial Setup",
                    "Exchange rate is required when moving to ORDERED.",
                ),
                (
                    "purchase_order_invoice_file",
                    "PO Invoice File",
                    "Attachments",
                    "PO invoice file is required when moving to ORDERED.",
                ),
                (
                    "invoice_number",
                    "Invoice Number",
                    "Logistics & Dates",
                    "Invoice number is required when moving to ORDERED.",
                ),
                (
                    "invoice_date",
                    "Invoice Date",
                    "Logistics & Dates",
                    "Invoice date is required when moving to ORDERED.",
                ),
                (
                    "commission_fee_pct",
                    "Commission %",
                    "Financial Setup",
                    "Commission % is required when moving to ORDERED.",
                ),
                (
                    "forwarder_name",
                    "Forwarder",
                    "General",
                    "Forwarder name is required when moving to ORDERED.",
                ),
                (
                    "supplier_name",
                    "Supplier",
                    "General",
                    "Supplier name is required when moving to ORDERED.",
                ),
                (
                    "shop_services",
                    "Jasa Belanja",
                    "General",
                    "Jasa belanja is required when moving to ORDERED.",
                ),
                (
                    "delivery_fee",
                    "Delivery Fee (RMB)",
                    "Financial Setup",
                    "Delivery fee is required when moving to ORDERED. Can be 0.",
                ),
            ]:
                if not _present(field):
                    missing.append(
                        {"field": field, "label": label, "section": section, "message": message}
                    )

            # Order details: need at least one existing or incoming
            has_incoming_details = bool(data.get("order_details"))
            has_existing_details = (
                po.order_details.exists() if hasattr(po, "order_details") else False
            )
            if not has_incoming_details and not has_existing_details:
                missing.append(
                    {
                        "field": "order_details",
                        "label": "Order Items",
                        "section": "Order Items",
                        "message": "At least one order item is required when moving to ORDERED.",
                    }
                )

        elif new_status == PurchaseOrder.POStatus.SHIPPED:
            for field, label, section, message in [
                (
                    "delivery_order_number",
                    "Delivery Order No.",
                    "Logistics & Dates",
                    "Delivery order number is required when moving to SHIPPED.",
                ),
                (
                    "delivery_order_file",
                    "Delivery Order File",
                    "Attachments",
                    "Delivery order file is required when moving to SHIPPED.",
                ),
                (
                    "shipping_fee_per_cbm",
                    "Shipping Fee / CBM",
                    "Financial Setup",
                    "Shipping fee per CBM is required when moving to SHIPPED.",
                ),
                ("cbm", "CBM", "Logistics & Dates", "CBM is required when moving to SHIPPED."),
                (
                    "weight",
                    "Weight (kg)",
                    "Logistics & Dates",
                    "Weight is required when moving to SHIPPED.",
                ),
            ]:
                if not _present(field):
                    missing.append(
                        {"field": field, "label": label, "section": section, "message": message}
                    )

        elif new_status == PurchaseOrder.POStatus.DELIVERED:
            if not _present("delivery_order_invoice_file"):
                missing.append(
                    {
                        "field": "delivery_order_invoice_file",
                        "label": "DO Invoice File",
                        "section": "Attachments",
                        "message": "Delivery order invoice file is required when moving to DELIVERED.",
                    }
                )

            if po.order_details.filter(
                sourcing_item__isnull=False, product_variant__isnull=True
            ).exists():
                missing.append(
                    {
                        "field": "order_details",
                        "label": "Draft Lines",
                        "section": "Order Items",
                        "message": "All sourcing draft items must be finalized before marking as DELIVERED.",
                    }
                )

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
                if detail.product_variant_id is None:  # type: ignore[attr-defined]
                    continue
                received = detail.received_qty or 0
                if received < detail.ordered_qty:
                    partial_items.append(
                        {
                            "name": detail.product_variant.name,  # type: ignore[union-attr]
                            "ordered_qty": detail.ordered_qty,
                            "received_qty": received,
                        }
                    )
            if partial_items:
                warnings.append(
                    {
                        "type": "partial_receipt",
                        "message": f"{len(partial_items)} item(s) have received qty less than ordered qty.",
                        "items": partial_items,
                    }
                )

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
        new_has_discount = data.get("has_discount")

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

        if old_status not in [PurchaseOrder.POStatus.DRAFT]:
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
                                            "order_details": f"Cannot change {field} when status is {old_status}. Price fields can only be changed in DRAFT status."
                                        }
                                    )

        if new_status in [PurchaseOrder.POStatus.DELIVERED, PurchaseOrder.POStatus.COMPLETED]:
            existing_details_map = {str(d.id): d for d in po.order_details.all()}

            product_variant_ids = list(
                set(
                    d.product_variant.id  # type: ignore[union-attr]
                    for d in po.order_details.all()
                    if d.product_variant is not None
                )
            )

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
                if existing_detail.product_variant_id is None:  # type: ignore[attr-defined]
                    continue  # draft lines have no received_qty to check

                ordered_qty = detail_data.get("ordered_qty", existing_detail.ordered_qty)
                received_qty = detail_data.get("received_qty", existing_detail.received_qty or 0)
                existing_received_qty = existing_detail.received_qty or 0

                if received_qty < existing_received_qty:
                    qty_decrease = existing_received_qty - received_qty
                    pvw = pvw_map.get(str(existing_detail.product_variant.id))  # type: ignore[union-attr]
                    if pvw:
                        if pvw.physical_qty < qty_decrease:
                            raise ValidationError(
                                {
                                    "order_details": f"Cannot decrease received_qty for {existing_detail.product_variant.name}. "  # type: ignore[union-attr]
                                    f"Physical qty ({pvw.physical_qty}) is less than the decrease amount ({qty_decrease}). "
                                    f"There may be sales on this item."
                                }
                            )

                    cogs = cogs_map.get(str(existing_detail.product_variant.id))  # type: ignore[union-attr]
                    if cogs:
                        if cogs.remaining_qty < qty_decrease:
                            raise ValidationError(
                                {
                                    "order_details": f"Cannot decrease received_qty for {existing_detail.product_variant.name}. "  # type: ignore[union-attr]
                                    f"COGS remaining_qty ({cogs.remaining_qty}) is less than the decrease amount ({qty_decrease}). "
                                    f"There may be sales on this item."
                                }
                            )

                if received_qty > ordered_qty:
                    remarks = detail_data.get("remarks") or existing_detail.remarks
                    if not remarks:
                        raise ValidationError(
                            {
                                "order_details": f"Remarks is required for {existing_detail.product_variant.name} when received_qty ({received_qty}) exceeds ordered_qty ({ordered_qty})."  # type: ignore[union-attr]
                            }
                        )

                if new_status == PurchaseOrder.POStatus.COMPLETED and received_qty < ordered_qty:
                    remarks = detail_data.get("remarks") or existing_detail.remarks
                    if not remarks:
                        raise ValidationError(
                            {
                                "order_details": f"Remarks is required for {existing_detail.product_variant.name} when moving to COMPLETED status with partial delivery (received_qty: {received_qty}, ordered_qty: {ordered_qty})."  # type: ignore[union-attr]
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
                if detail.product_variant_id is None:  # type: ignore[attr-defined]
                    continue
                inventory_data.append(
                    {
                        "product_variant_id": detail.product_variant.id,  # type: ignore[union-attr]
                        "ordered_qty": detail.ordered_qty,
                        "note": f"Stock movement for PO {po.purchase_order_number} purchase",
                    }
                )

            # Create AccountsPayable when PO moves to ORDERED
            from apps.finance.services.accounts_payable_service import AccountsPayableService

            AccountsPayableService().create_payable_from_po(po)
            self._snapshot_sales_metrics_at_ordered(po)

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
                    discounted_total_price_base = (
                        existing_detail_obj.discounted_total_price_base or 0
                    )

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
                if detail.product_variant_id is None:  # type: ignore[attr-defined]
                    continue
                inventory_data.append(
                    {
                        "product_variant_id": detail.product_variant.id,  # type: ignore[union-attr]
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

        if "exchange_rate" in updated_fields and po.exchange_rate:
            self._recalculate_item_prices(po)

        if details_data is not None:
            self._update_order_details(po, details_data, old_status, new_status or old_status)

        if new_has_discount is False:
            po.order_details.update(
                discounted_unit_price_foreign=None,
                discounted_unit_price_base=None,
                discounted_total_price_foreign=None,
                discounted_total_price_base=None,
            )

        self._recalculate_forecast_cbm(po)
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
                    discounted_total_price_base = (
                        existing_detail_obj.discounted_total_price_base or 0
                    )

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
                if po.status in [PurchaseOrder.POStatus.DRAFT, PurchaseOrder.POStatus.ORDERED]:
                    product_variant_id = ulid_field.to_python(detail_data.get("product_variant_id"))
                    detail_data_copy = {
                        k: v for k, v in detail_data.items() if k != "product_variant_id"
                    }
                    if po.exchange_rate:
                        exchange_rate = Decimal(str(po.exchange_rate))
                        unit_price_foreign = Decimal(
                            str(detail_data_copy.get("unit_price_foreign") or 0)
                        )
                        disc_foreign = Decimal(
                            str(detail_data_copy.get("discounted_unit_price_foreign") or 0)
                        )
                        if not disc_foreign:
                            disc_foreign = unit_price_foreign
                        ordered_qty = int(detail_data_copy.get("ordered_qty") or 0)
                        detail_data_copy["unit_price_base"] = int(
                            round(unit_price_foreign * exchange_rate)
                        )
                        detail_data_copy["discounted_unit_price_foreign"] = disc_foreign
                        detail_data_copy["discounted_unit_price_base"] = int(
                            round(disc_foreign * exchange_rate)
                        )
                        detail_data_copy["total_price_foreign"] = unit_price_foreign * ordered_qty
                        detail_data_copy["discounted_total_price_foreign"] = (
                            disc_foreign * ordered_qty
                        )
                        detail_data_copy["total_price_base"] = (
                            detail_data_copy["unit_price_base"] * ordered_qty
                        )
                        detail_data_copy["discounted_total_price_base"] = (
                            detail_data_copy["discounted_unit_price_base"] * ordered_qty
                        )
                    detail = PurchaseOrderDetail(
                        purchase_order=po,
                        product_variant_id=product_variant_id,
                        company=po.company,
                        **detail_data_copy,
                    )
                    new_details.append(detail)

        if po.status in [PurchaseOrder.POStatus.DRAFT, PurchaseOrder.POStatus.ORDERED]:
            ids_to_keep = [d.id for d in update_details] + [d.id for d in new_details]
            # Only delete non-draft lines (product_variant is set); draft lines are managed separately
            po.order_details.filter(product_variant__isnull=False).exclude(
                id__in=ids_to_keep
            ).delete()

        if update_details:
            update_fields_list = list(update_fields_set) + ["udate"]
            PurchaseOrderDetail.objects.bulk_update(
                update_details, update_fields_list, batch_size=100
            )

        # Populate supplier_link for new details from ProductSupplier
        if new_details:
            from apps.inventory.models import ProductSupplier as PS

            real_new_details = [d for d in new_details if d.product_variant_id is not None]  # type: ignore[attr-defined]
            variant_ids = [d.product_variant.id for d in real_new_details]  # type: ignore[union-attr]
            raw_pairs = (
                list(
                    ProductVariant.objects.filter(id__in=variant_ids).values_list(
                        "id", "product_id"
                    )
                )
                if variant_ids
                else []
            )
            variant_product_map: dict[str, str] = {str(vid): str(pid) for vid, pid in raw_pairs}
            product_ids = list(set(variant_product_map.values()))
            link_map: dict[str, str | None] = {}
            if po.supplier and product_ids:
                for row in PS.objects.filter(
                    product_id__in=product_ids,
                    supplier=po.supplier,
                    supplier_link__isnull=False,
                ).values("product_id", "supplier_link"):
                    link_map[str(row["product_id"])] = row["supplier_link"]
                missing = [pid for pid in product_ids if pid not in link_map]
                if missing:
                    for row in PS.objects.filter(
                        product_id__in=missing,
                        supplier_link__isnull=False,
                    ).values("product_id", "supplier_link"):
                        link_map.setdefault(str(row["product_id"]), row["supplier_link"])
            for detail in real_new_details:
                if not detail.supplier_link:
                    product_id = variant_product_map.get(str(detail.product_variant.id))  # type: ignore[union-attr]
                    if product_id:
                        detail.supplier_link = link_map.get(product_id)

        if new_details:
            PurchaseOrderDetail.objects.bulk_create(new_details, batch_size=100)
            new_variant_ids = [
                str(d.product_variant.id)  # type: ignore[union-attr]
                for d in new_details
                if d.product_variant is not None
            ]
            self._ensure_product_supplier_links(po, new_variant_ids)

        if po.status in (PurchaseOrder.POStatus.DRAFT, PurchaseOrder.POStatus.ORDERED):
            all_saved_details = list(po.order_details.all())
            self._sync_variant_prices(po, all_saved_details)

    def _sync_variant_prices(
        self,
        po: PurchaseOrder,
        all_details: list[PurchaseOrderDetail],
    ) -> None:
        """Persist the latest unit price back to each variant for auto-fill."""
        from django.utils import timezone

        now = timezone.now()

        for detail in all_details:
            if detail.product_variant_id is None:  # type: ignore[attr-defined]
                continue
            if not detail.unit_price_foreign:
                continue
            currency = po.currency or ""
            ProductVariant.objects.filter(
                id=detail.product_variant.id,  # type: ignore[union-attr]
            ).filter(Q(price_updated_at__isnull=True) | Q(price_updated_at__lt=now)).update(
                last_unit_price_foreign=detail.unit_price_foreign,
                last_discounted_unit_price_foreign=detail.discounted_unit_price_foreign or None,
                last_currency=currency,
                price_updated_at=now,
            )

    def _recalculate_item_prices(self, po: "PurchaseOrder") -> None:
        """Recalculate all item base prices using the PO's current exchange_rate.

        Called when exchange_rate is set or changed on a DRAFT PO so that
        items created before the rate was known get their IDR prices filled in.
        """
        if not po.exchange_rate:
            return
        exchange_rate = Decimal(str(po.exchange_rate))
        details = list(po.order_details.all())
        to_update = []
        for detail in details:
            if not detail.unit_price_foreign:
                continue
            unit_price_foreign = Decimal(str(detail.unit_price_foreign))
            disc_foreign = Decimal(str(detail.discounted_unit_price_foreign or unit_price_foreign))
            ordered_qty = detail.ordered_qty or 0
            detail.unit_price_base = int(round(unit_price_foreign * exchange_rate))
            detail.discounted_unit_price_base = int(round(disc_foreign * exchange_rate))
            detail.total_price_foreign = unit_price_foreign * ordered_qty
            detail.discounted_total_price_foreign = disc_foreign * ordered_qty
            detail.total_price_base = detail.unit_price_base * ordered_qty
            detail.discounted_total_price_base = detail.discounted_unit_price_base * ordered_qty
            to_update.append(detail)
        if to_update:
            PurchaseOrderDetail.objects.bulk_update(
                to_update,
                [
                    "unit_price_base",
                    "discounted_unit_price_base",
                    "total_price_foreign",
                    "discounted_total_price_foreign",
                    "total_price_base",
                    "discounted_total_price_base",
                ],
                batch_size=100,
            )

    @staticmethod
    def _calc_shipping_fee(shipping_fee_per_cbm: Decimal, cbm: Decimal) -> int:
        """Tiered freight calculation.

        < 0.1 CBM  : charge as if 0.1 CBM (minimum charge)
        0.1–0.5 CBM: cbm × rate + 100,000 IDR surcharge
        ≥ 0.5 CBM  : cbm × rate
        """
        if cbm <= 0 or shipping_fee_per_cbm <= 0:
            return 0
        if cbm < Decimal("0.1"):
            fee = Decimal("0.1") * shipping_fee_per_cbm
        elif cbm < Decimal("0.5"):
            fee = cbm * shipping_fee_per_cbm + Decimal("100000")
        else:
            fee = cbm * shipping_fee_per_cbm
        return int(round(fee))

    def _recalculate_forecast_cbm(self, po: PurchaseOrder) -> None:
        total_cbm = Decimal("0")
        has_dimensions = False
        for detail in po.order_details.all().select_related("product_variant__product"):
            if detail.product_variant_id is None:  # type: ignore[attr-defined]
                continue
            product = detail.product_variant.product  # type: ignore[union-attr]
            if product.length > 0 and product.width > 0 and product.height > 0:
                has_dimensions = True
                volume_m3 = (
                    Decimal(str(product.length))
                    * Decimal(str(product.width))
                    * Decimal(str(product.height))
                    / Decimal("1000000")
                )
                total_cbm += volume_m3 * detail.ordered_qty
        if has_dimensions:
            po.forecast_cbm = round(total_cbm, 6)
            po.save(update_fields=["forecast_cbm", "udate"])

    def _recalculate_po_totals(self, po: PurchaseOrder) -> None:
        """Recalculate PO totals based on order details and fee fields."""
        total_ordered_qty = 0
        total_received_qty = 0
        total_item_amount = 0
        total_item_rmb = Decimal("0")

        for detail in po.order_details.all():
            total_ordered_qty += detail.ordered_qty or 0
            total_received_qty += detail.received_qty or 0
            if po.has_discount:
                total_item_amount += (
                    detail.discounted_total_price_base
                    if detail.discounted_total_price_base is not None
                    else (detail.total_price_base or 0)
                )
                total_item_rmb += Decimal(
                    str(
                        detail.discounted_total_price_foreign
                        if detail.discounted_total_price_foreign is not None
                        else (detail.total_price_foreign or 0)
                    )
                )
            else:
                total_item_amount += detail.total_price_base or 0
                total_item_rmb += Decimal(str(detail.total_price_foreign or 0))

        exchange_rate = Decimal(str(po.exchange_rate or 0))
        delivery_fee_idr = int(round(Decimal(str(po.delivery_fee or 0)) * exchange_rate))
        commission_fee_pct = Decimal(str(po.commission_fee_pct or 0))
        shipping_fee_per_cbm = Decimal(
            str(po.shipping_fee_per_cbm or po.forecast_shipping_fee_per_cbm or 0)
        )
        cbm = Decimal(str(po.cbm or po.forecast_cbm or 0))

        commission_fee = int(
            round(commission_fee_pct / Decimal("100") * total_item_rmb * exchange_rate)
        )
        shipping_fee = PurchaseOrderService._calc_shipping_fee(shipping_fee_per_cbm, cbm)
        procure_amount = shipping_fee + commission_fee + delivery_fee_idr
        total_order_amount = total_item_amount + commission_fee + delivery_fee_idr
        total_amount = total_item_amount + commission_fee + shipping_fee + delivery_fee_idr

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
