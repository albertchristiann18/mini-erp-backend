from decimal import Decimal
from typing import Any, Dict

from rest_framework import serializers

from apps.inventory.models import ProductVariant, Warehouse
from apps.purchasing.models import PurchaseOrder, PurchaseOrderDetail
from apps.purchasing.services.purchasing_service import PurchaseOrderService
from core.models import Company
from core.utils import compress_pdf_file


class PurchaseOrderDetailSerializer(serializers.ModelSerializer):
    """Serializer for Purchase Order Details"""

    id = serializers.CharField(required=False)
    product_variant_id = serializers.CharField(write_only=True)
    product_variant_name = serializers.CharField(source="product_variant.name", read_only=True)
    updated_qty = serializers.IntegerField(read_only=True)

    class Meta:
        model = PurchaseOrderDetail
        fields = [
            "id",
            "product_variant_id",
            "product_variant_name",
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

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        return self._calculate_prices(attrs)

    def create(self, validated_data: Dict[str, Any]) -> PurchaseOrderDetail:
        validated_data = self._calculate_prices(validated_data)
        product_variant_id = validated_data.pop("product_variant_id")
        product_variant = ProductVariant.objects.get(id=product_variant_id)
        validated_data["product_variant"] = product_variant
        return super().create(validated_data)  # type: ignore

    def _calculate_prices(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate all price fields based on input values.

        Input fields (user provides):
        - unit_price_foreign (mandatory)
        - discounted_unit_price_foreign (optional, defaults to unit_price_foreign)
        - ordered_qty

        Calculated fields (only if exchange_rate is provided):
        - unit_price_base = unit_price_foreign * exchange_rate (from parent PO)
        - discounted_unit_price_foreign = unit_price_foreign if not provided
        - discounted_unit_price_base = discounted_unit_price_foreign * exchange_rate
        - total_price_foreign = unit_price_foreign * ordered_qty
        - discounted_total_price_foreign = discounted_unit_price_foreign * ordered_qty
        - total_price_base = unit_price_base * ordered_qty
        - discounted_total_price_base = discounted_unit_price_base * ordered_qty

        If exchange_rate is not provided, these fields will be left blank.
        """
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
            "cdate",
            "udate",
        ]
        read_only_fields = ["id", "cdate", "udate"]

    def get_cost_ratio_cogs(self, obj: PurchaseOrder) -> float:
        return float(obj.cost_ratio_cogs())

    def get_shipping_per_qty(self, obj: PurchaseOrder) -> int:
        return obj.get_shipping_per_qty()


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
        ]
        extra_kwargs = {
            "purchase_order_number": {"required": False},
            "purchase_order_invoice_file": {"required": False},
            "delivery_order_file": {"required": False},
            "delivery_order_invoice_file": {"required": False},
            "packing_list_file": {"required": False},
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

        forwarder_name = attrs.get("forwarder_name")
        if not forwarder_name:
            raise serializers.ValidationError({"forwarder_name": "Forwarder name is required."})

        shop_services = attrs.get("shop_services")
        if not shop_services:
            raise serializers.ValidationError({"shop_services": "Shop services is required."})

        commission_fee_pct = attrs.get("commission_fee_pct")
        if commission_fee_pct is None:
            raise serializers.ValidationError(
                {"commission_fee_pct": "Commission fee percentage is required."}
            )

        delivery_fee = attrs.get("delivery_fee")
        if delivery_fee is None:
            raise serializers.ValidationError({"delivery_fee": "Delivery fee is required."})

        currency = attrs.get("currency")
        if not currency:
            raise serializers.ValidationError({"currency": "Currency is required."})

        return attrs

    def _calculate_totals_from_details(self, order_details: list) -> dict:
        """Calculate totals from order details."""
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
        """Calculate PO totals based on order details and fee fields."""
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
        shipping_fee = int(round(shipping_fee_per_cbm * cbm)) if cbm else 0
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
            "commission_fee_rmb",
            "delivery_order_number",
            "order_details",
            "purchase_order_invoice_file",
            "delivery_order_file",
            "delivery_order_invoice_file",
            "packing_list_file",
        ]
        extra_kwargs = {
            "purchase_order_number": {"required": False},
            "purchase_order_invoice_file": {"required": False},
            "delivery_order_file": {"required": False},
            "delivery_order_invoice_file": {"required": False},
            "packing_list_file": {"required": False},
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
            allowed = PurchaseOrderService.STATUS_TRANSITIONS.get(current_status, [])
            if new_status not in allowed:
                raise serializers.ValidationError(
                    {
                        "status": f"Cannot transition from {current_status} to {new_status}. "
                        f"Allowed transitions: {', '.join(allowed) if allowed else 'none'}"
                    }
                )

            if (
                new_status == PurchaseOrder.POStatus.ORDERED
                and current_status == PurchaseOrder.POStatus.DRAFT
            ):
                current_exchange_rate = self.instance.exchange_rate
                new_exchange_rate = attrs.get("exchange_rate")
                if current_exchange_rate is None and new_exchange_rate is None:
                    raise serializers.ValidationError(
                        {
                            "exchange_rate": "Exchange rate is required when moving to ORDERED status. Please set exchange_rate on the Purchase Order."
                        }
                    )

                current_invoice_file = self.instance.purchase_order_invoice_file
                new_invoice_file = attrs.get("purchase_order_invoice_file")
                if not current_invoice_file and new_invoice_file is None:
                    raise serializers.ValidationError(
                        {
                            "purchase_order_invoice_file": "Invoice file is required when moving to ORDERED status."
                        }
                    )

                current_invoice_number = self.instance.invoice_number
                new_invoice_number = attrs.get("invoice_number")
                if not current_invoice_number and not new_invoice_number:
                    raise serializers.ValidationError(
                        {
                            "invoice_number": "Invoice number is required when moving to ORDERED status."
                        }
                    )

                current_invoice_date = self.instance.invoice_date
                new_invoice_date = attrs.get("invoice_date")
                if not current_invoice_date and not new_invoice_date:
                    raise serializers.ValidationError(
                        {"invoice_date": "Invoice date is required when moving to ORDERED status."}
                    )

                current_commission_fee_pct = self.instance.commission_fee_pct
                new_commission_fee_pct = attrs.get("commission_fee_pct")
                if current_commission_fee_pct is None and new_commission_fee_pct is None:
                    raise serializers.ValidationError(
                        {
                            "commission_fee_pct": "Commission fee percentage is required when moving to ORDERED status."
                        }
                    )

                current_forwarder_name = self.instance.forwarder_name
                new_forwarder_name = attrs.get("forwarder_name")
                if not current_forwarder_name and not new_forwarder_name:
                    raise serializers.ValidationError(
                        {
                            "forwarder_name": "Forwarder name is required when moving to ORDERED status."
                        }
                    )

                current_supplier_name = self.instance.supplier_name
                new_supplier_name = attrs.get("supplier_name")
                if not current_supplier_name and not new_supplier_name:
                    raise serializers.ValidationError(
                        {
                            "supplier_name": "Supplier name is required when moving to ORDERED status."
                        }
                    )

                current_shop_services = self.instance.shop_services
                new_shop_services = attrs.get("shop_services")
                if not current_shop_services and not new_shop_services:
                    raise serializers.ValidationError(
                        {
                            "shop_services": "Shop services is required when moving to ORDERED status."
                        }
                    )

                current_delivery_fee = self.instance.delivery_fee
                new_delivery_fee = attrs.get("delivery_fee")
                if current_delivery_fee is None and new_delivery_fee is None:
                    raise serializers.ValidationError(
                        {
                            "delivery_fee": "Delivery fee is required when moving to ORDERED status. Can be set to 0."
                        }
                    )

            elif (
                new_status == PurchaseOrder.POStatus.SHIPPED
                and current_status == PurchaseOrder.POStatus.ORDERED
            ):
                current_do_number = self.instance.delivery_order_number
                new_do_number = attrs.get("delivery_order_number")
                if not current_do_number and not new_do_number:
                    raise serializers.ValidationError(
                        {
                            "delivery_order_number": "Delivery order number is required when moving to SHIPPED status."
                        }
                    )

                current_do_file = self.instance.delivery_order_file
                new_do_file = attrs.get("delivery_order_file")
                if not current_do_file and new_do_file is None:
                    raise serializers.ValidationError(
                        {
                            "delivery_order_file": "Delivery order file is required when moving to SHIPPED status."
                        }
                    )

                current_shipping_fee_per_cbm = self.instance.shipping_fee_per_cbm
                new_shipping_fee_per_cbm = attrs.get("shipping_fee_per_cbm")
                if not current_shipping_fee_per_cbm and new_shipping_fee_per_cbm is None:
                    raise serializers.ValidationError(
                        {
                            "shipping_fee_per_cbm": "Shipping fee per CBM is required when moving to SHIPPED status."
                        }
                    )

                current_cbm = self.instance.cbm
                new_cbm = attrs.get("cbm")
                if not current_cbm and new_cbm is None:
                    raise serializers.ValidationError(
                        {"cbm": "CBM is required when moving to SHIPPED status."}
                    )

                current_weight = self.instance.weight
                new_weight = attrs.get("weight")
                if not current_weight and new_weight is None:
                    raise serializers.ValidationError(
                        {"weight": "Weight is required when moving to SHIPPED status."}
                    )

            elif (
                new_status == PurchaseOrder.POStatus.DELIVERED
                and current_status == PurchaseOrder.POStatus.SHIPPED
            ):
                current_do_invoice = self.instance.delivery_order_invoice_file
                new_do_invoice = attrs.get("delivery_order_invoice_file")
                if not current_do_invoice and new_do_invoice is None:
                    raise serializers.ValidationError(
                        {
                            "delivery_order_invoice_file": "Delivery order invoice file is required when moving to DELIVERED status."
                        }
                    )

        if current_status != PurchaseOrder.POStatus.DRAFT:
            new_exchange_rate = attrs.get("exchange_rate")
            if new_exchange_rate is not None:
                raise serializers.ValidationError(
                    {
                        "exchange_rate": f"Cannot change exchange_rate when status is {current_status}. Exchange rate can only be changed in DRAFT status."
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

        if order_details and current_status not in [PurchaseOrder.POStatus.DRAFT]:
            incoming_ids = {str(d.get("id")) for d in order_details if d.get("id")}
            if incoming_ids:
                new_details = [d for d in order_details if not d.get("id")]
                if new_details:
                    raise serializers.ValidationError(
                        {
                            "order_details": f"Cannot add new details when status is {current_status}. Only DRAFT status allows adding new details."
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
        """Calculate totals from order details."""
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
        """Calculate PO totals based on order details and fee fields."""
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
        shipping_fee = int(round(shipping_fee_per_cbm * cbm)) if cbm else 0
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


class PurchaseOrderReadSerializer(serializers.ModelSerializer):
    """Serializer for reading Purchase Orders with all details"""

    order_details = PurchaseOrderDetailSerializer(many=True, read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)
    cost_ratio_cogs = serializers.SerializerMethodField()
    shipping_per_qty = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrder
        fields = [
            "id",
            "cost_ratio_cogs",
            "shipping_per_qty",
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
            "commission_fee_rmb",
            "order_details",
            "purchase_order_invoice_file",
            "delivery_order_file",
            "delivery_order_invoice_file",
            "packing_list_file",
            "cdate",
            "udate",
        ]
        read_only_fields = ["id", "cdate", "udate"]

    def get_cost_ratio_cogs(self, obj: PurchaseOrder) -> float:
        return float(obj.cost_ratio_cogs())

    def get_shipping_per_qty(self, obj: PurchaseOrder) -> int:
        return obj.get_shipping_per_qty()
