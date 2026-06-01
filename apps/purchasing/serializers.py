from decimal import Decimal
from typing import Any

from rest_framework import serializers

from apps.inventory.models import ProductVariant, Warehouse
from apps.purchasing.models import PurchaseOrder, PurchaseOrderDetail, PurchaseOrderStatusHistory
from apps.purchasing.services.purchasing_service import PurchaseOrderService
from core.models import Company
from core.utils import compress_pdf_file


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
    product_variant_id = serializers.CharField(write_only=True)
    product_variant_name = serializers.CharField(source="product_variant.name", read_only=True)
    product_id = serializers.CharField(source="product_variant.product.id", read_only=True)
    product_name = serializers.CharField(source="product_variant.product.name", read_only=True)
    product_supplier_link = serializers.CharField(
        source="product_variant.product.supplier_link", read_only=True, allow_null=True
    )
    product_photo_url = serializers.SerializerMethodField()
    updated_qty = serializers.IntegerField(read_only=True)

    class Meta:
        model = PurchaseOrderDetail
        fields = [
            "id",
            "product_variant_id",
            "product_variant_name",
            "product_id",
            "product_name",
            "product_supplier_link",
            "product_photo_url",
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
        ]
        read_only_fields = ["updated_qty"]

    def get_product_photo_url(self, obj: PurchaseOrderDetail) -> str | None:
        photo = obj.product_variant.product.product_photo
        return photo.url if photo else None

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        return self._calculate_prices(attrs)

    def create(self, validated_data: dict[str, Any]) -> PurchaseOrderDetail:
        validated_data = self._calculate_prices(validated_data)
        product_variant_id = validated_data.pop("product_variant_id")
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
        attrs["total_price_base"] = attrs["unit_price_base"] * ordered_qty
        attrs["discounted_total_price_base"] = attrs["discounted_unit_price_base"] * ordered_qty

        return attrs


class PurchaseOrderListSerializer(serializers.ModelSerializer):
    """Serializer for listing Purchase Orders (lightweight, no details)"""

    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)
    cost_ratio_cogs = serializers.SerializerMethodField()
    shipping_per_qty = serializers.SerializerMethodField()
    delivery_fee_idr = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrder
        fields = [
            "id",
            "purchase_order_number",
            "status",
            "warehouse_name",
            "company_name",
            "supplier_name",
            "invoice_number",
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

    class Meta:
        model = PurchaseOrder
        fields = [
            "purchase_order_number",
            "warehouse_id",
            "company_id",
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


class PurchaseOrderUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating Purchase Orders and Details"""

    order_details = PurchaseOrderDetailSerializer(many=True, required=False)
    warehouse_id = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = PurchaseOrder
        fields = [
            "purchase_order_number",
            "status",
            "warehouse_id",
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
            "forecast_shipping_fee",
            "cogs_ratio_forecast",
            "commission_fee_rmb",
            "delivery_order_number",
            "order_details",
            "purchase_order_invoice_file",
            "delivery_order_file",
            "delivery_order_invoice_file",
            "packing_list_file",
            "note",
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
        ]

    def validate(self, attrs: dict) -> dict:
        if not self.instance:
            return attrs

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
            if new_exchange_rate is not None:
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
            if field in ("status", "order_details", "warehouse_id", "_purchase_order"):
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

        return attrs

    @staticmethod
    def _compress_file(value: Any) -> Any:
        if value:
            return compress_pdf_file(value)
        return value

    def validate_purchase_order_invoice_file(self, value: Any) -> Any:
        return self._compress_file(value)

    def validate_delivery_order_file(self, value: Any) -> Any:
        return self._compress_file(value)

    def validate_delivery_order_invoice_file(self, value: Any) -> Any:
        return self._compress_file(value)

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
            "cogs_ratio_forecast",
            "commission_fee_rmb",
            "order_details",
            "purchase_order_invoice_file",
            "delivery_order_file",
            "delivery_order_invoice_file",
            "packing_list_file",
            "note",
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
