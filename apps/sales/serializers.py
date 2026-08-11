from decimal import Decimal

from rest_framework import serializers

from apps.sales.models import (
    SalesOrder,
    SalesOrderCogsDetail,
    SalesOrderItem,
    SalesReturn,
    SalesReturnItem,
)
from apps.sales.services.sales_service import SalesOrderService

# ---- Sales Order Item ----


class SalesOrderItemSerializer(serializers.ModelSerializer):
    id = serializers.CharField(required=False)
    product_variant_id = serializers.CharField(write_only=True)
    product_variant_name = serializers.CharField(source="product_variant.name", read_only=True)

    class Meta:
        model = SalesOrderItem
        fields = [
            "id",
            "product_variant_id",
            "product_variant_name",
            "quantity",
            "selling_price",
            "discount_amount",
            "commission_fee",
            "service_fee",
            "total_marketplace_fee",
            "actual_cogs_per_unit",
            "actual_cogs_total",
            "line_total",
        ]
        read_only_fields = [
            "total_marketplace_fee",
            "actual_cogs_per_unit",
            "actual_cogs_total",
            "line_total",
        ]


class SalesOrderCogsDetailSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    product_cogs = serializers.CharField(read_only=True)

    class Meta:
        model = SalesOrderCogsDetail
        fields = [
            "id",
            "product_cogs",
            "quantity_consumed",
            "cogs_per_unit",
            "total_cogs",
        ]
        read_only_fields = fields


# ---- Sales Order ----


class SalesOrderCreateSerializer(serializers.ModelSerializer):
    items = SalesOrderItemSerializer(many=True, write_only=True, required=True)
    warehouse_id = serializers.CharField(write_only=True)
    company_id = serializers.CharField(write_only=True)
    marketplace_id = serializers.CharField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = SalesOrder
        fields = [
            "warehouse_id",
            "company_id",
            "marketplace_id",
            "source_platform",
            "marketplace_order_id",
            "marketplace_order_number",
            "customer_name",
            "customer_phone",
            "shipping_address",
            "shipping_province",
            "shipping_city",
            "order_date",
            "courier_name",
            "tracking_number",
            "shipping_fee",
            "shipping_fee_seller",
            "note",
            "items",
        ]

    def create(self, validated_data: dict) -> SalesOrder:
        service = SalesOrderService()
        return service.create_sales_order(validated_data)


class SalesOrderUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesOrder
        fields = [
            "status",
            "customer_name",
            "customer_phone",
            "shipping_address",
            "shipping_province",
            "shipping_city",
            "courier_name",
            "tracking_number",
            "shipping_fee",
            "shipping_fee_seller",
            "note",
        ]
        extra_kwargs = {field: {"required": False} for field in fields}

    def validate(self, attrs: dict) -> dict:
        if not self.instance:
            return attrs

        new_status = attrs.get("status")
        if new_status and new_status != self.instance.status:
            allowed = SalesOrderService.STATUS_TRANSITIONS.get(self.instance.status, [])
            if new_status not in allowed:
                raise serializers.ValidationError(
                    {
                        "status": f"Cannot transition from {self.instance.status} to {new_status}. "
                        f"Allowed: {', '.join(allowed) if allowed else 'none'}"
                    }
                )
        return attrs


class SalesOrderItemDetailSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    product_variant = serializers.CharField(read_only=True)
    product_variant_name = serializers.CharField(source="product_variant.name", read_only=True)
    cogs_details = SalesOrderCogsDetailSerializer(many=True, read_only=True)

    class Meta:
        model = SalesOrderItem
        fields = [
            "id",
            "product_variant",
            "product_variant_name",
            "quantity",
            "selling_price",
            "discount_amount",
            "commission_fee",
            "service_fee",
            "total_marketplace_fee",
            "actual_cogs_per_unit",
            "actual_cogs_total",
            "line_total",
            "cogs_details",
        ]


class SalesReturnItemSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    sales_order_item = serializers.CharField(read_only=True)
    product_variant = serializers.CharField(read_only=True)
    product_variant_name = serializers.CharField(source="product_variant.name", read_only=True)

    class Meta:
        model = SalesReturnItem
        fields = [
            "id",
            "sales_order_item",
            "product_variant",
            "product_variant_name",
            "quantity",
            "reversed_cogs_total",
        ]
        read_only_fields = ["reversed_cogs_total", "product_variant"]


class SalesReturnSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    sales_order = serializers.CharField(read_only=True)
    items = SalesReturnItemSerializer(many=True, read_only=True)

    class Meta:
        model = SalesReturn
        fields = [
            "id",
            "return_number",
            "sales_order",
            "status",
            "reason",
            "return_date",
            "refund_amount",
            "note",
            "items",
            "cdate",
            "udate",
        ]
        read_only_fields = ["id", "return_number", "return_date", "cdate", "udate"]


class SalesOrderDetailSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    items = SalesOrderItemDetailSerializer(many=True, read_only=True)
    returns = SalesReturnSerializer(many=True, read_only=True)
    warehouse = serializers.CharField(read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    marketplace = serializers.CharField(read_only=True)

    class Meta:
        model = SalesOrder
        fields = [
            "id",
            "order_number",
            "marketplace",
            "source_platform",
            "marketplace_order_id",
            "marketplace_order_number",
            "status",
            "warehouse",
            "warehouse_name",
            "customer_name",
            "customer_phone",
            "shipping_address",
            "shipping_province",
            "shipping_city",
            "order_date",
            "confirmed_date",
            "shipped_date",
            "delivered_date",
            "completed_date",
            "courier_name",
            "tracking_number",
            "shipping_fee",
            "shipping_fee_seller",
            "subtotal",
            "total_discount",
            "total_marketplace_fee",
            "total_cogs",
            "net_revenue",
            "gross_profit",
            "note",
            "items",
            "returns",
            "cdate",
            "udate",
        ]
        read_only_fields = ["id", "order_number", "cdate", "udate"]


class SalesOrderListSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)

    class Meta:
        model = SalesOrder
        fields = [
            "id",
            "order_number",
            "status",
            "warehouse_name",
            "customer_name",
            "marketplace_order_id",
            "marketplace_order_number",
            "subtotal",
            "net_revenue",
            "gross_profit",
            "order_date",
            "cdate",
            "udate",
        ]
        read_only_fields = ["id", "order_number", "cdate", "udate"]


# ---- Sales Return Create ----


class SalesReturnItemCreateSerializer(serializers.Serializer):
    sales_order_item_id = serializers.CharField()
    quantity = serializers.IntegerField(min_value=1)


class SalesReturnCreateSerializer(serializers.Serializer):
    sales_order_id = serializers.CharField()
    reason = serializers.CharField(required=False, default="")
    refund_amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, default=Decimal("0")
    )
    note = serializers.CharField(required=False, default="")
    items = SalesReturnItemCreateSerializer(many=True)
