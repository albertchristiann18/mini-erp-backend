from rest_framework import serializers

from apps.inventory.models import (
    StockMovement,
    Warehouse,
)
from apps.purchasing.models import ProductSupplier, Supplier


class WarehouseSerializer(serializers.ModelSerializer):
    company = serializers.UUIDField(source="company.id", read_only=True)

    class Meta:
        model = Warehouse
        fields = "__all__"


class StockMovementSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True)
    company = serializers.UUIDField(source="company.id", read_only=True)
    product_variant = serializers.UUIDField(source="product_variant.id", read_only=True)
    product_variant_name = serializers.CharField(source="product_variant.name", read_only=True)
    warehouse = serializers.UUIDField(source="warehouse.id", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    movement_type = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = [
            "id",
            "company",
            "product_variant",
            "product_variant_name",
            "warehouse",
            "warehouse_name",
            "movement_type",
            "quantity",
            "reference_number",
            "note",
            "balance_before",
            "balance_after",
            "cdate",
        ]
        read_only_fields = fields

    def get_movement_type(self, obj: StockMovement) -> str:
        return StockMovement.MovementType(obj.movement_type).name


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

    def create(self, validated_data: dict) -> "ProductSupplier":
        company = self.context["request"].user.profile.company
        return ProductSupplier.objects.create(company=company, **validated_data)
