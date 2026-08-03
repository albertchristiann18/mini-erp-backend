from decimal import Decimal
from typing import Any

from rest_framework import serializers

from apps.catalog.models import ProductVariant
from apps.inventory.models import Warehouse
from apps.purchasing.models import (
    ProductSupplier,
    PurchaseOrder,
    PurchaseOrderDetail,
    PurchaseOrderStatusHistory,
    Supplier,
)
from apps.purchasing.services.purchasing_service import PurchaseOrderService
from core.models import Company
from core.utils import compress_pdf_iterative


class SupplierSerializer(serializers.ModelSerializer):
    company_id = serializers.CharField(source="company.id", read_only=True)

    class Meta:
        model = Supplier
        fields = [
            "id",
            "company_id",
            "name",
            "contact_name",
            "phone",
            "country",
            "notes",
            "supplier_link",
            "is_active",
            "cdate",
            "udate",
        ]
        read_only_fields = ["id", "cdate", "udate"]


class ProductSupplierSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    product_id = serializers.CharField(write_only=True)
    supplier_id = serializers.CharField(write_only=True)

    class Meta:
        model = ProductSupplier
        fields = [
            "id",
            "product_id",
            "supplier_id",
            "supplier_name",
            "supplier_link",
            "cdate",
            "udate",
        ]
        read_only_fields = ["id", "cdate", "udate"]

    def create(self, validated_data: dict[str, Any]) -> "ProductSupplier":
        company = self.context["request"].user.profile.company
        return ProductSupplier.objects.create(company=company, **validated_data)


def _calc_shipping_fee(shipping_fee_per_cbm: Decimal, cbm: Decimal) -> int:
    """Tiered freight: <0.1->min 0.1 CBM, 0.1-0.5->ratexcbm+100000, >=0.5->ratexcbm."""
    if cbm <= 0 or shipping_fee_per_cbm <= 0:
        return 0
    if cbm < Decimal("0.1"):
        fee = Decimal("0.1") * shipping_fee_per_cbm
    elif cbm < Decimal("0.5"):
        fee = cbm * shipping_fee_per_cbm + Decimal("100000")
    else:
        fee = cbm * shipping_fee_per_cbm
    return int(round(fee))


class PurchaseOrderDetailSerializer(serializers.ModelSerializer):
    """Serializer for Purchase Order Details"""

    id = serializers.CharField(required=False)
    product_variant_id = serializers.CharField(
        write_only=True, required=False, allow_null=True, allow_blank=True
    )
    variant_id = serializers.SerializerMethodField()
    product_variant_name = serializers.SerializerMethodField()
    sku_variant_code = serializers.SerializerMethodField()
    product_id = serializers.SerializerMethodField()
    product_name = serializers.SerializerMethodField()
    product_supplier_link = serializers.CharField(
        source="supplier_link", read_only=True, allow_null=True
    )
    variant_values = serializers.SerializerMethodField()
    product_photo_url = serializers.SerializerMethodField()
    last_unit_price_foreign = serializers.SerializerMethodField()
    last_currency = serializers.SerializerMethodField()
    last_discounted_unit_price_foreign = serializers.SerializerMethodField()
    updated_qty = serializers.IntegerField(read_only=True)
    product_dim1_key = serializers.SerializerMethodField()

    def get_product_dim1_key(self, obj: "PurchaseOrderDetail") -> str | None:
        if not obj.product_variant_id:  # type: ignore[attr-defined]
            return None
        return obj.product_variant.product.dim1_key or None  # type: ignore[union-attr]

    class Meta:
        model = PurchaseOrderDetail
        fields = [
            "id",
            "product_variant_id",
            "variant_id",
            "product_variant_name",
            "sku_variant_code",
            "product_id",
            "product_name",
            "product_supplier_link",
            "product_photo_url",
            "variant_values",
            "ordered_qty",
            "received_qty",
            "updated_qty",
            "unit_price_foreign",
            "unit_price_base",
            "discounted_unit_price_foreign",
            "discounted_unit_price_base",
            "total_price_foreign",
            "total_price_base",
            "discounted_total_price_foreign",
            "discounted_total_price_base",
            "remarks",
            "avg_sales",
            "avg_sales_7d",
            "stock_on_hand",
            "incoming_qty",
            "last_unit_price_foreign",
            "last_currency",
            "last_discounted_unit_price_foreign",
            "product_dim1_key",
        ]
        read_only_fields = [
            "updated_qty",
            "avg_sales",
            "avg_sales_7d",
            "stock_on_hand",
            "incoming_qty",
            "variant_id",
            "product_dim1_key",
        ]

    def get_variant_id(self, obj: PurchaseOrderDetail) -> str | None:
        return str(obj.product_variant.id) if obj.product_variant_id else None  # type: ignore[union-attr, attr-defined]

    def get_product_variant_name(self, obj: PurchaseOrderDetail) -> str | None:
        return obj.product_variant.name if obj.product_variant_id else None  # type: ignore[union-attr, attr-defined]

    def get_sku_variant_code(self, obj: PurchaseOrderDetail) -> str | None:
        return obj.product_variant.sku_variant_code if obj.product_variant_id else None  # type: ignore[union-attr, attr-defined]

    def get_product_id(self, obj: PurchaseOrderDetail) -> str | None:
        return str(obj.product_variant.product.id) if obj.product_variant_id else None  # type: ignore[union-attr, attr-defined]

    def get_product_name(self, obj: PurchaseOrderDetail) -> str | None:
        return obj.product_variant.product.name if obj.product_variant_id else None  # type: ignore[union-attr, attr-defined]

    def get_variant_values(self, obj: PurchaseOrderDetail) -> dict | None:
        return obj.product_variant.variant_values if obj.product_variant_id else None  # type: ignore[union-attr, attr-defined]

    def get_product_photo_url(self, obj: "PurchaseOrderDetail") -> str | None:
        if not obj.product_variant_id:  # type: ignore[attr-defined]
            return None
        variant = obj.product_variant
        product = variant.product  # type: ignore[union-attr]

        # 1. Per-dimension-value image (highest priority)
        dim1_key = product.dim1_key  # type: ignore[union-attr]
        if dim1_key:
            dim1_value = (variant.variant_values or {}).get(dim1_key)  # type: ignore[union-attr]
            if dim1_value:
                # Iterate the prefetch cache — do NOT call .filter() or .order_by()
                for dim_img in product.dimension_images.all():  # type: ignore[union-attr]
                    if dim_img.dim_key == dim1_key and dim_img.dim_value == dim1_value:
                        return dim_img.photo.url  # type: ignore[no-any-return]

        # 2. Variant-level photo
        if variant.photo:  # type: ignore[union-attr]
            return variant.photo.url  # type: ignore[union-attr, no-any-return]

        # 3. Product gallery — use prefetch cache, sort in Python (avoids bypassing prefetch)
        gallery = sorted(product.photos.all(), key=lambda p: p.order)  # type: ignore[union-attr]
        if gallery:
            return gallery[0].image.url  # type: ignore[no-any-return]

        # 4. Product-level photo field
        if variant.product.product_photo:  # type: ignore[union-attr]
            return variant.product.product_photo.url  # type: ignore[union-attr, no-any-return]

        return None

    def get_last_unit_price_foreign(self, obj: PurchaseOrderDetail) -> str | None:
        if not obj.product_variant_id:  # type: ignore[attr-defined]
            return None
        val = obj.product_variant.last_unit_price_foreign  # type: ignore[union-attr]
        return str(val) if val is not None else None

    def get_last_currency(self, obj: PurchaseOrderDetail) -> str | None:
        if not obj.product_variant_id:  # type: ignore[attr-defined]
            return None
        return obj.product_variant.last_currency  # type: ignore[union-attr]

    def get_last_discounted_unit_price_foreign(self, obj: PurchaseOrderDetail) -> str | None:
        if not obj.product_variant_id:  # type: ignore[attr-defined]
            return None
        val = obj.product_variant.last_discounted_unit_price_foreign  # type: ignore[union-attr]
        return str(val) if val is not None else None

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs.get("id") and not attrs.get("product_variant_id"):
            raise serializers.ValidationError(
                {"product_variant_id": "This field is required when adding a new order line."}
            )
        return self._calculate_prices(attrs)

    def create(self, validated_data: dict[str, Any]) -> PurchaseOrderDetail:
        validated_data = self._calculate_prices(validated_data)
        product_variant_id = validated_data.pop("product_variant_id", None)
        if product_variant_id:
            product_variant = ProductVariant.objects.get(id=product_variant_id)
            validated_data["product_variant"] = product_variant
        return super().create(validated_data)  # type: ignore

    def _calculate_prices(self, attrs: dict[str, Any]) -> dict[str, Any]:
        unit_price_foreign = attrs.get("unit_price_foreign")
        ordered_qty = attrs.get("ordered_qty", 0) or 0

        if unit_price_foreign is None:
            return attrs

        purchase_order = (
            attrs.get("_purchase_order")
            or self.context.get("purchase_order")
            or getattr(self, "_mock_po", None)
        )
        if not purchase_order:
            return attrs

        exchange_rate = getattr(purchase_order, "exchange_rate", None)
        if exchange_rate is None:
            return attrs

        unit_price_foreign = Decimal(str(unit_price_foreign))
        exchange_rate = Decimal(str(exchange_rate))

        attrs["unit_price_base"] = int(round(unit_price_foreign * exchange_rate))

        discounted_unit_price_foreign = attrs.get("discounted_unit_price_foreign")
        if discounted_unit_price_foreign is None:
            discounted_unit_price_foreign = unit_price_foreign
        else:
            discounted_unit_price_foreign = Decimal(str(discounted_unit_price_foreign))

        attrs["discounted_unit_price_foreign"] = discounted_unit_price_foreign
        attrs["discounted_unit_price_base"] = int(
            round(discounted_unit_price_foreign * exchange_rate)
        )

        attrs["total_price_foreign"] = unit_price_foreign * ordered_qty
        attrs["discounted_total_price_foreign"] = discounted_unit_price_foreign * ordered_qty
        attrs["total_price_base"] = int(round(unit_price_foreign * exchange_rate * ordered_qty))
        attrs["discounted_total_price_base"] = int(
            round(discounted_unit_price_foreign * exchange_rate * ordered_qty)
        )

        return attrs


class PurchaseOrderListSerializer(serializers.ModelSerializer):
    """Serializer for listing Purchase Orders (lightweight, no details)"""

    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)
    cost_ratio_cogs = serializers.SerializerMethodField()
    shipping_per_qty = serializers.SerializerMethodField()
    delivery_fee_idr = serializers.SerializerMethodField()
    supplier_id = serializers.CharField(source="supplier.id", read_only=True, allow_null=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "id",
            "purchase_order_number",
            "status",
            "warehouse_name",
            "company_name",
            "supplier_name",
            "supplier_id",
            "invoice_number",
            "delivery_order_number",
            "invoice_date",
            "delivery_date",
            "forecast_delivery_date",
            "forwarder_name",
            "exchange_rate",
            "cbm",
            "forecast_cbm",
            "shipping_fee",
            "procure_amount",
            "total_item_amount",
            "commission_fee",
            "total_ordered_qty",
            "total_amount",
            "cost_ratio_cogs",
            "shipping_per_qty",
            "delivery_fee_idr",
            "cdate",
            "udate",
            "note",
            "has_discount",
        ]
        read_only_fields = ["id", "cdate", "udate"]

    def get_cost_ratio_cogs(self, obj: PurchaseOrder) -> float:
        return float(obj.cost_ratio_cogs())

    def get_shipping_per_qty(self, obj: PurchaseOrder) -> int:
        return obj.get_shipping_per_qty()

    def get_delivery_fee_idr(self, obj: PurchaseOrder) -> int:
        delivery_fee = obj.delivery_fee or Decimal("0")
        exchange_rate = obj.exchange_rate or Decimal("0")
        return int(round(Decimal(str(delivery_fee)) * Decimal(str(exchange_rate))))


class PurchaseOrderCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating Purchase Orders with nested details"""

    order_details = PurchaseOrderDetailSerializer(many=True, write_only=True, required=True)
    warehouse_id = serializers.CharField(write_only=True)
    company_id = serializers.CharField(write_only=True)
    supplier_id = serializers.CharField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "purchase_order_number",
            "warehouse_id",
            "company_id",
            "supplier_id",
            "supplier_name",
            "forwarder_name",
            "shop_services",
            "commission_fee_pct",
            "commission_fee",
            "delivery_fee",
            "currency",
            "exchange_rate",
            "cbm",
            "weight",
            "shipping_fee_per_cbm",
            "shipping_fee",
            "total_ordered_qty",
            "total_received_qty",
            "total_item_amount",
            "total_order_amount",
            "total_amount",
            "procure_amount",
            "order_details",
            "purchase_order_invoice_file",
            "delivery_order_file",
            "delivery_order_invoice_file",
            "packing_list_file",
            "note",
            "has_discount",
        ]
        extra_kwargs = {
            "purchase_order_number": {"required": False},
            "purchase_order_invoice_file": {"required": False},
            "delivery_order_file": {"required": False},
            "delivery_order_invoice_file": {"required": False},
            "packing_list_file": {"required": False},
            "note": {"required": False},
        }
        read_only_fields = [
            "purchase_order_number",
            "total_ordered_qty",
            "total_received_qty",
            "total_item_amount",
            "total_order_amount",
            "total_amount",
            "procure_amount",
            "commission_fee",
            "shipping_fee",
        ]

    def validate(self, attrs: dict) -> dict:
        if attrs.get("status") and attrs.get("status") != PurchaseOrder.POStatus.DRAFT:
            raise serializers.ValidationError(
                {"status": "Purchase Order must be created with DRAFT status"}
            )

        supplier_id = attrs.pop("supplier_id", None)
        if supplier_id:
            try:
                supplier = Supplier.objects.get(id=supplier_id)
                attrs["supplier"] = supplier
                attrs["supplier_name"] = supplier.name
            except Supplier.DoesNotExist:
                raise serializers.ValidationError({"supplier_id": "Supplier not found."})

        return attrs

    def _calculate_totals_from_details(self, order_details: list) -> dict:
        total_ordered_qty = 0
        total_received_qty = 0
        total_item_amount = 0

        for detail in order_details:
            ordered_qty = detail.get("ordered_qty", 0) or 0
            received_qty = detail.get("received_qty", 0) or 0
            discounted_total_price_base = detail.get("discounted_total_price_base") or 0

            total_ordered_qty += ordered_qty
            total_received_qty += received_qty
            total_item_amount += discounted_total_price_base

        return {
            "total_ordered_qty": total_ordered_qty,
            "total_received_qty": total_received_qty,
            "total_item_amount": total_item_amount,
        }

    def _calculate_po_totals(self, attrs: dict) -> dict:
        order_details = attrs.get("order_details", [])
        totals = self._calculate_totals_from_details(order_details)

        exchange_rate = Decimal(str(attrs.get("exchange_rate") or 0))
        commission_fee_pct = Decimal(str(attrs.get("commission_fee_pct") or 0))
        shipping_fee_per_cbm = Decimal(str(attrs.get("shipping_fee_per_cbm") or 0))
        cbm = Decimal(str(attrs.get("cbm") or 0))

        total_item_rmb = Decimal("0")
        for detail in order_details:
            total_item_rmb += Decimal(str(detail.get("discounted_total_price_foreign") or 0))
        commission_fee = int(round(commission_fee_pct / 100 * total_item_rmb * exchange_rate))
        shipping_fee = _calc_shipping_fee(shipping_fee_per_cbm, cbm) if cbm else 0
        procure_amount = shipping_fee + commission_fee
        total_order_amount = totals["total_item_amount"] + commission_fee
        total_amount = totals["total_item_amount"] + commission_fee + shipping_fee

        return {
            "total_ordered_qty": totals["total_ordered_qty"],
            "total_received_qty": totals["total_received_qty"],
            "total_item_amount": totals["total_item_amount"],
            "commission_fee": commission_fee,
            "shipping_fee": shipping_fee,
            "shipping_fee_per_cbm": int(shipping_fee_per_cbm),
            "procure_amount": procure_amount,
            "total_order_amount": total_order_amount,
            "total_amount": total_amount,
        }

    def create(self, validated_data: dict) -> PurchaseOrder:
        order_details_data = validated_data.pop("order_details", [])
        warehouse_id = validated_data.pop("warehouse_id")
        company_id = validated_data.pop("company_id")

        totals = self._calculate_po_totals(validated_data)
        validated_data.update(totals)

        warehouse = Warehouse.objects.get(id=warehouse_id)
        company = Company.objects.get(id=company_id)

        validated_data.setdefault("status", PurchaseOrder.POStatus.DRAFT)
        po = PurchaseOrder.objects.create(warehouse=warehouse, company=company, **validated_data)

        if order_details_data:
            for detail_data in order_details_data:
                detail_data["_purchase_order"] = po
                detail_serializer = PurchaseOrderDetailSerializer(data=detail_data)
                if detail_serializer.is_valid():
                    detail_serializer.save(purchase_order=po, company=company)

        return po

    def to_internal_value(self, data: dict) -> dict[str, Any]:
        ret: dict[str, Any] = super().to_internal_value(data)
        exchange_rate = ret.get("exchange_rate")
        self._mock_po = type("PO", (), {"exchange_rate": exchange_rate})()
        return ret

    def to_representation(self, instance: PurchaseOrder) -> dict[str, Any]:
        ret: dict[str, Any] = super().to_representation(instance)
        ret["_purchase_order"] = instance
        return ret


PDF_COMPRESS_THRESHOLD_MB = 2.0


class PurchaseOrderUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating Purchase Orders and Details"""

    order_details = PurchaseOrderDetailSerializer(many=True, required=False)
    warehouse_id = serializers.CharField(write_only=True, required=False)
    supplier_id = serializers.CharField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "purchase_order_number",
            "status",
            "warehouse_id",
            "supplier_id",
            "supplier_name",
            "forwarder_name",
            "shop_services",
            "commission_fee_pct",
            "commission_fee",
            "delivery_fee",
            "currency",
            "exchange_rate",
            "cbm",
            "weight",
            "shipping_fee_per_cbm",
            "shipping_fee",
            "total_ordered_qty",
            "total_received_qty",
            "total_item_amount",
            "total_order_amount",
            "total_amount",
            "procure_amount",
            "invoice_number",
            "invoice_date",
            "delivery_date",
            "forecast_delivery_date",
            "forecast_cbm",
            "forecast_shipping_fee_per_cbm",
            "forecast_shipping_fee",
            "commission_fee_rmb",
            "delivery_order_number",
            "order_details",
            "purchase_order_invoice_file",
            "delivery_order_file",
            "delivery_order_invoice_file",
            "packing_list_file",
            "note",
            "has_discount",
        ]
        extra_kwargs = {
            "purchase_order_number": {"required": False},
            "purchase_order_invoice_file": {"required": False},
            "delivery_order_file": {"required": False},
            "delivery_order_invoice_file": {"required": False},
            "packing_list_file": {"required": False},
            "note": {"required": False},
        }
        read_only_fields = [
            "total_ordered_qty",
            "total_received_qty",
            "total_item_amount",
            "total_order_amount",
            "total_amount",
            "procure_amount",
            "commission_fee",
            "shipping_fee",
            "forecast_shipping_fee",
        ]

    def validate(self, attrs: dict) -> dict:
        if not self.instance:
            return attrs

        supplier_id = attrs.pop("supplier_id", None)
        if supplier_id is not None:
            try:
                supplier = Supplier.objects.get(id=supplier_id)
                attrs["supplier"] = supplier
                attrs["supplier_name"] = supplier.name
            except Supplier.DoesNotExist:
                raise serializers.ValidationError({"supplier_id": "Supplier not found."})

        current_status = self.instance.status
        new_status = attrs.get("status")

        if new_status is not None and new_status != current_status:
            allowed = PurchaseOrder.STATUS_TRANSITIONS.get(current_status, [])
            if new_status not in allowed:
                raise serializers.ValidationError(
                    {
                        "status": f"Cannot transition from {current_status} to {new_status}. "
                        f"Allowed transitions: {', '.join(allowed) if allowed else 'none'}"
                    }
                )

            service = PurchaseOrderService()
            missing = service.check_purchase_order_requirements(
                self.instance, new_status, incoming_data=attrs
            )
            if missing:
                raise serializers.ValidationError(
                    {item["field"]: item["message"] for item in missing}
                )

        if current_status != PurchaseOrder.POStatus.DRAFT:
            new_exchange_rate = attrs.get("exchange_rate")
            if new_exchange_rate is not None and new_exchange_rate != self.instance.exchange_rate:
                raise serializers.ValidationError(
                    {
                        "exchange_rate": f"Cannot change exchange_rate when status is {current_status}. Exchange rate can only be changed in DRAFT status."
                    }
                )

        editable = PurchaseOrder.get_editable_fields(current_status)
        editable_header_set = set(editable["header"])
        editable_detail_set = set(editable["order_detail"])
        if new_status and new_status != current_status:
            target_editable = PurchaseOrder.get_editable_fields(new_status)
            editable_header_set |= set(target_editable["header"])
            editable_detail_set |= set(target_editable["order_detail"])

        locked_violations = []
        for field, value in attrs.items():
            if field in ("status", "order_details", "warehouse_id", "_purchase_order", "supplier"):
                continue
            if value is not None and field not in editable_header_set:
                locked_violations.append(field)

        if locked_violations:
            raise serializers.ValidationError(
                {
                    field: f"'{field}' cannot be edited when PO status is {current_status}."
                    for field in locked_violations
                }
            )

        order_details = attrs.get("order_details", [])
        for detail_data in order_details:
            if not detail_data.get("id"):
                continue
            for field in detail_data:
                if field in (
                    "id",
                    "product_variant_id",
                    "received_date",
                    "received_qty",
                    "remarks",
                ):
                    continue
                if field not in editable_detail_set and detail_data.get(field) is not None:
                    raise serializers.ValidationError(
                        {
                            "order_details": f"'{field}' cannot be edited when PO status is {current_status}."
                        }
                    )

        if new_status not in [
            PurchaseOrder.POStatus.SHIPPED,
            PurchaseOrder.POStatus.DELIVERED,
            None,
        ]:
            order_details = attrs.get("order_details", [])
            for detail_data in order_details:
                if detail_data.get("received_qty") is not None:
                    raise serializers.ValidationError(
                        {
                            "order_details": "received_qty can only be provided when status is SHIPPED or DELIVERED."
                        }
                    )

        order_details = attrs.get("order_details")

        if (
            new_status == PurchaseOrder.POStatus.ORDERED
            and current_status == PurchaseOrder.POStatus.DRAFT
        ):
            existing_details_count = self.instance.order_details.count() if self.instance else 0
            if not order_details and existing_details_count == 0:
                raise serializers.ValidationError(
                    {
                        "order_details": "At least one order detail is required when moving to ORDERED status."
                    }
                )
            if order_details:
                for detail_data in order_details:
                    detail_id = detail_data.get("id")
                    if detail_id:
                        new_ordered_qty = detail_data.get("ordered_qty")
                        if new_ordered_qty is not None:
                            raise serializers.ValidationError(
                                {
                                    "order_details": "Cannot change ordered_qty when transitioning from DRAFT to ORDERED status."
                                }
                            )

        if order_details and current_status not in [
            PurchaseOrder.POStatus.DRAFT,
            PurchaseOrder.POStatus.ORDERED,
        ]:
            new_details = [d for d in order_details if not d.get("id")]
            if new_details:
                raise serializers.ValidationError(
                    {
                        "order_details": f"Cannot add new details when status is {current_status}. Only DRAFT or ORDERED status allows adding new details."
                    }
                )

        if self.instance and new_status in [
            PurchaseOrder.POStatus.ORDERED,
            PurchaseOrder.POStatus.SHIPPED,
            PurchaseOrder.POStatus.DELIVERED,
        ]:
            existing_totals = {
                "total_ordered_qty": self.instance.total_ordered_qty or 0,
                "total_received_qty": self.instance.total_received_qty or 0,
                "total_item_amount": self.instance.total_item_amount or 0,
            }
            attrs.setdefault("exchange_rate", self.instance.exchange_rate)
            attrs.setdefault("commission_fee_pct", self.instance.commission_fee_pct)
            attrs.setdefault("delivery_fee", self.instance.delivery_fee)
            attrs.setdefault("shipping_fee_per_cbm", self.instance.shipping_fee_per_cbm)
            attrs.setdefault("cbm", self.instance.cbm)
            attrs.setdefault("shipping_fee", self.instance.shipping_fee)

            totals = self._calculate_po_totals(attrs, existing_totals)
            attrs.update(totals)

        # Auto-recalculate freight whenever real or forecast CBM/rate values change.
        # Priority: real cbm + real rate > forecast cbm + forecast rate.
        # Skipped for status transitions that already called _calculate_po_totals above.
        if self.instance and new_status not in [
            PurchaseOrder.POStatus.ORDERED,
            PurchaseOrder.POStatus.SHIPPED,
            PurchaseOrder.POStatus.DELIVERED,
        ]:
            cbm_trigger_fields = {
                "cbm",
                "shipping_fee_per_cbm",
                "forecast_cbm",
                "forecast_shipping_fee_per_cbm",
            }
            if cbm_trigger_fields & set(attrs.keys()):
                real_cbm = attrs.get("cbm") if "cbm" in attrs else self.instance.cbm
                real_per_cbm = (
                    attrs.get("shipping_fee_per_cbm")
                    if "shipping_fee_per_cbm" in attrs
                    else self.instance.shipping_fee_per_cbm
                )
                forecast_cbm_v = (
                    attrs.get("forecast_cbm")
                    if "forecast_cbm" in attrs
                    else self.instance.forecast_cbm
                )
                forecast_per_cbm_v = (
                    attrs.get("forecast_shipping_fee_per_cbm")
                    if "forecast_shipping_fee_per_cbm" in attrs
                    else self.instance.forecast_shipping_fee_per_cbm
                )
                effective_cbm = real_cbm if real_cbm else forecast_cbm_v
                effective_per_cbm = real_per_cbm if real_per_cbm else forecast_per_cbm_v
                if effective_cbm and effective_per_cbm:
                    new_shipping_fee = _calc_shipping_fee(
                        Decimal(str(effective_per_cbm)), Decimal(str(effective_cbm))
                    )
                    exchange_rate = Decimal(
                        str(attrs.get("exchange_rate") or self.instance.exchange_rate or 0)
                    )
                    commission_fee_pct = Decimal(
                        str(
                            attrs.get("commission_fee_pct") or self.instance.commission_fee_pct or 0
                        )
                    )
                    total_item_rmb = Decimal("0")
                    for detail in self.instance.order_details.all():
                        total_item_rmb += Decimal(str(detail.discounted_total_price_foreign or 0))
                    commission_fee = int(
                        round(commission_fee_pct / 100 * total_item_rmb * exchange_rate)
                    )
                    total_item_amount = self.instance.total_item_amount or 0
                    attrs["shipping_fee"] = new_shipping_fee
                    attrs["procure_amount"] = new_shipping_fee + commission_fee
                    attrs["total_order_amount"] = total_item_amount + commission_fee
                    attrs["total_amount"] = total_item_amount + commission_fee + new_shipping_fee

        if self.instance:
            forecast_cbm_val = (
                attrs.get("forecast_cbm") if "forecast_cbm" in attrs else self.instance.forecast_cbm
            )
            forecast_per_cbm_val = (
                attrs.get("forecast_shipping_fee_per_cbm")
                if "forecast_shipping_fee_per_cbm" in attrs
                else self.instance.forecast_shipping_fee_per_cbm
            )
            if forecast_cbm_val and forecast_per_cbm_val:
                attrs["forecast_shipping_fee"] = _calc_shipping_fee(
                    Decimal(str(forecast_per_cbm_val)), Decimal(str(forecast_cbm_val))
                )

        return attrs

    def _compress_file(self, field_name: str, value: Any) -> Any:
        if not value:
            return value
        file_size_mb = (value.size or 0) / (1024 * 1024)
        if file_size_mb <= PDF_COMPRESS_THRESHOLD_MB:
            return value
        result, was_compressed = compress_pdf_iterative(value, target_mb=PDF_COMPRESS_THRESHOLD_MB)
        if was_compressed:
            if not hasattr(self, "_compressed_fields"):
                self._compressed_fields: list[str] = []
            self._compressed_fields.append(field_name)
        return result

    def validate_purchase_order_invoice_file(self, value: Any) -> Any:
        return self._compress_file("purchase_order_invoice_file", value)

    def validate_delivery_order_file(self, value: Any) -> Any:
        return self._compress_file("delivery_order_file", value)

    def validate_delivery_order_invoice_file(self, value: Any) -> Any:
        return self._compress_file("delivery_order_invoice_file", value)

    def validate_packing_list_file(self, value: Any) -> Any:
        return self._compress_file("packing_list_file", value)

    def _calculate_totals_from_details(
        self, order_details: list, existing_details_map: dict | None = None
    ) -> dict:
        total_ordered_qty = 0
        total_received_qty = 0
        total_item_amount = 0

        for detail in order_details:
            detail_id = detail.get("id")
            existing_detail = (
                existing_details_map.get(detail_id)
                if (detail_id and existing_details_map)
                else None
            )

            ordered_qty = detail.get("ordered_qty") or (
                getattr(existing_detail, "ordered_qty", 0) if existing_detail else 0
            )
            received_qty = detail.get("received_qty") or (
                getattr(existing_detail, "received_qty", 0) if existing_detail else 0
            )
            discounted_total_price_base = detail.get("discounted_total_price_base") or (
                getattr(existing_detail, "discounted_total_price_base", 0) if existing_detail else 0
            )

            total_ordered_qty += ordered_qty
            total_received_qty += received_qty
            total_item_amount += discounted_total_price_base

        return {
            "total_ordered_qty": total_ordered_qty,
            "total_received_qty": total_received_qty,
            "total_item_amount": total_item_amount,
        }

    def _calculate_po_totals(self, attrs: dict, existing_totals: dict | None = None) -> dict:
        exchange_rate = Decimal(str(attrs.get("exchange_rate") or 0))
        commission_fee_pct = Decimal(str(attrs.get("commission_fee_pct") or 0))
        shipping_fee_per_cbm = Decimal(str(attrs.get("shipping_fee_per_cbm") or 0))
        cbm = Decimal(str(attrs.get("cbm") or 0))

        order_details = attrs.get("order_details") or []
        total_item_rmb = Decimal("0")
        if order_details:
            for detail in order_details:
                total_item_rmb += Decimal(str(detail.get("discounted_total_price_foreign") or 0))
        else:
            po = getattr(self, "instance", None)
            if po:
                for detail in po.order_details.all():
                    total_item_rmb += Decimal(str(detail.discounted_total_price_foreign or 0))
        commission_fee = int(round(commission_fee_pct / 100 * total_item_rmb * exchange_rate))
        shipping_fee = _calc_shipping_fee(shipping_fee_per_cbm, cbm) if cbm else 0
        procure_amount = shipping_fee + commission_fee
        total_item_amount = existing_totals.get("total_item_amount", 0) if existing_totals else 0
        total_order_amount = total_item_amount + commission_fee
        total_amount = total_item_amount + commission_fee + shipping_fee

        return {
            "total_ordered_qty": existing_totals.get("total_ordered_qty", 0)
            if existing_totals
            else 0,
            "total_received_qty": existing_totals.get("total_received_qty", 0)
            if existing_totals
            else 0,
            "total_item_amount": total_item_amount,
            "commission_fee": commission_fee,
            "shipping_fee": shipping_fee,
            "shipping_fee_per_cbm": int(shipping_fee_per_cbm),
            "procure_amount": procure_amount,
            "total_order_amount": total_order_amount,
            "total_amount": total_amount,
        }

    def to_internal_value(self, data: dict) -> dict[str, Any]:
        ret: dict[str, Any] = super().to_internal_value(data)
        ret["_purchase_order"] = self.instance
        return ret


class PurchaseOrderStatusHistorySerializer(serializers.ModelSerializer):
    changed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrderStatusHistory
        fields = ["id", "from_status", "to_status", "changed_by_name", "note", "cdate"]

    def get_changed_by_name(self, obj: PurchaseOrderStatusHistory) -> str | None:
        if obj.changed_by:
            return obj.changed_by.get_full_name() or obj.changed_by.username
        return None


class PurchaseOrderReadSerializer(serializers.ModelSerializer):
    """Serializer for reading Purchase Orders with all details"""

    order_details = PurchaseOrderDetailSerializer(many=True, read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)
    cost_ratio_cogs = serializers.SerializerMethodField()
    shipping_per_qty = serializers.SerializerMethodField()
    status_history = PurchaseOrderStatusHistorySerializer(many=True, read_only=True)
    next_status = serializers.SerializerMethodField()
    editable_fields = serializers.SerializerMethodField()
    supplier_id = serializers.CharField(source="supplier.id", read_only=True, allow_null=True)

    def to_representation(self, instance: PurchaseOrder) -> dict[str, Any]:
        ret: dict[str, Any] = super().to_representation(instance)
        freight_map = self._build_freight_map(instance)
        for item in ret.get("order_details") or []:
            detail_id = str(item.get("id", ""))
            f = freight_map.get(detail_id, {})
            item["shipping_per_unit_idr"] = f.get("shipping_per_unit_idr")
            item["delivery_per_unit_idr"] = f.get("delivery_per_unit_idr")
            item["commission_per_unit_idr"] = f.get("commission_per_unit_idr")
            item["cogs_per_unit_idr"] = f.get("cogs_per_unit_idr")
            item["product_has_dimensions"] = f.get("product_has_dimensions")
        return ret

    class Meta:
        model = PurchaseOrder
        fields = [
            "id",
            "cost_ratio_cogs",
            "shipping_per_qty",
            "status_history",
            "next_status",
            "purchase_order_number",
            "status",
            "warehouse_name",
            "company_name",
            "supplier_name",
            "supplier_id",
            "forwarder_name",
            "shop_services",
            "commission_fee_pct",
            "commission_fee",
            "delivery_fee",
            "currency",
            "exchange_rate",
            "cbm",
            "weight",
            "shipping_fee_per_cbm",
            "shipping_fee",
            "total_ordered_qty",
            "total_received_qty",
            "total_item_amount",
            "total_order_amount",
            "total_amount",
            "procure_amount",
            "invoice_number",
            "invoice_date",
            "delivery_order_number",
            "delivery_date",
            "forecast_delivery_date",
            "forecast_cbm",
            "forecast_shipping_fee",
            "forecast_shipping_fee_per_cbm",
            "commission_fee_rmb",
            "order_details",
            "purchase_order_invoice_file",
            "delivery_order_file",
            "delivery_order_invoice_file",
            "packing_list_file",
            "note",
            "has_discount",
            "editable_fields",
            "cdate",
            "udate",
        ]
        read_only_fields = ["id", "cdate", "udate"]

    def get_cost_ratio_cogs(self, obj: PurchaseOrder) -> float:
        return float(obj.cost_ratio_cogs())

    def get_shipping_per_qty(self, obj: PurchaseOrder) -> int:
        return obj.get_shipping_per_qty()

    def get_next_status(self, obj: PurchaseOrder) -> str | None:
        return obj.get_next_status()

    def get_editable_fields(self, obj: PurchaseOrder) -> dict[str, list[str]]:
        return PurchaseOrder.get_editable_fields(obj.status)

    @staticmethod
    def _build_freight_map(po: PurchaseOrder) -> dict[str, dict]:
        details = list(po.order_details.all())

        dims_map: dict[str, bool] = {}
        for detail in details:
            if detail.product_variant_id is None:  # type: ignore[attr-defined]
                dims_map[str(detail.id)] = False
                continue
            p = detail.product_variant.product  # type: ignore[union-attr]
            dims_map[str(detail.id)] = p.length > 0 and p.width > 0 and p.height > 0

        is_delivered = po.status in [
            PurchaseOrder.POStatus.DELIVERED,
            PurchaseOrder.POStatus.COMPLETED,
        ]

        if not is_delivered:
            return {
                detail_id: {
                    "shipping_per_unit_idr": None,
                    "delivery_per_unit_idr": None,
                    "commission_per_unit_idr": None,
                    "cogs_per_unit_idr": None,
                    "product_has_dimensions": has_dims,
                }
                for detail_id, has_dims in dims_map.items()
            }

        shipping_fee = Decimal(str(po.shipping_fee or 0))
        delivery_fee = Decimal(str(po.delivery_fee or 0))
        exchange_rate = Decimal(str(po.exchange_rate or 1))
        commission_fee_total = Decimal(str(po.commission_fee or 0))
        total_item_amount_idr = Decimal(str(po.total_item_amount or 0))
        delivery_fee_idr = delivery_fee * exchange_rate

        total_cbm = Decimal("0")
        item_data: dict[str, dict] = {}
        for detail in details:
            if detail.product_variant_id is None:  # type: ignore[attr-defined]
                item_data[str(detail.id)] = {
                    "cbm": Decimal("0"),
                    "item_value_idr": Decimal(str(detail.discounted_total_price_base or 0)),
                    "received_qty": 0,
                    "unit_price_idr": Decimal("0"),
                }
                continue
            p = detail.product_variant.product  # type: ignore[union-attr]
            length = Decimal(str(p.length or 0))
            width = Decimal(str(p.width or 0))
            height = Decimal(str(p.height or 0))
            ordered_qty = Decimal(str(detail.ordered_qty or 0))
            received_qty = detail.received_qty or 0
            cbm_per_unit = length * width * height / Decimal("1000000")
            detail_total_cbm = cbm_per_unit * ordered_qty
            total_cbm += detail_total_cbm
            item_data[str(detail.id)] = {
                "cbm": detail_total_cbm,
                "item_value_idr": Decimal(str(detail.discounted_total_price_base or 0)),
                "received_qty": received_qty,
                "unit_price_idr": Decimal(
                    str(detail.discounted_unit_price_base or detail.unit_price_base or 0)
                ),
            }

        result: dict[str, dict] = {}
        for detail in details:
            detail_id = str(detail.id)
            data = item_data[detail_id]
            received_qty = data["received_qty"]

            if received_qty <= 0:
                result[detail_id] = {
                    "shipping_per_unit_idr": None,
                    "delivery_per_unit_idr": None,
                    "commission_per_unit_idr": None,
                    "cogs_per_unit_idr": None,
                    "product_has_dimensions": dims_map[detail_id],
                }
                continue

            qty = Decimal(str(received_qty))

            shipping_share = (
                int(round(shipping_fee * data["cbm"] / total_cbm)) if total_cbm > 0 else 0
            )
            if total_item_amount_idr > 0:
                value_ratio = data["item_value_idr"] / total_item_amount_idr
                delivery_share = int(round(delivery_fee_idr * value_ratio))
                commission_share = int(round(commission_fee_total * value_ratio))
            else:
                delivery_share = 0
                commission_share = 0

            shipping_per_unit = int(round(Decimal(str(shipping_share)) / qty))
            delivery_per_unit = int(round(Decimal(str(delivery_share)) / qty))
            commission_per_unit = int(round(Decimal(str(commission_share)) / qty))
            cogs_per_unit = int(
                round(
                    data["unit_price_idr"]
                    + Decimal(str(shipping_share)) / qty
                    + Decimal(str(delivery_share)) / qty
                    + Decimal(str(commission_share)) / qty
                )
            )

            result[detail_id] = {
                "shipping_per_unit_idr": shipping_per_unit,
                "delivery_per_unit_idr": delivery_per_unit,
                "commission_per_unit_idr": commission_per_unit,
                "cogs_per_unit_idr": cogs_per_unit,
                "product_has_dimensions": dims_map[detail_id],
            }

        return result
