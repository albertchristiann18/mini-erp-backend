from rest_framework import serializers

from apps.inventory.models import (
    BusinessEntity,
    CompanyMarketplace,
    ProductBusinessEntity,
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


class CompanyMarketplaceSerializer(serializers.ModelSerializer):
    company_id = serializers.CharField(source="company.id", read_only=True)

    class Meta:
        model = CompanyMarketplace
        fields = ["id", "company_id", "name", "is_active", "cdate", "udate"]
        read_only_fields = ["id", "company_id", "cdate", "udate"]


class CompanyMarketplaceWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    is_active = serializers.BooleanField(default=True)


class BusinessEntitySerializer(serializers.ModelSerializer):
    company_id = serializers.CharField(source="company.id", read_only=True)
    marketplace_id = serializers.CharField(source="marketplace.id", read_only=True)
    marketplace_name = serializers.CharField(source="marketplace.name", read_only=True)

    class Meta:
        model = BusinessEntity
        fields = [
            "id",
            "company_id",
            "name",
            "marketplace_id",
            "marketplace_name",
            "is_active",
            "cdate",
            "udate",
        ]
        read_only_fields = ["id", "company_id", "cdate", "udate"]


class BusinessEntityWriteSerializer(serializers.ModelSerializer):
    marketplace_id = serializers.CharField(write_only=True)

    class Meta:
        model = BusinessEntity
        fields = ["name", "marketplace_id", "is_active"]

    def validate_marketplace_id(self, value: str) -> str:
        if not value:
            raise serializers.ValidationError("marketplace_id is required")
        return value


class ProductBusinessEntitySerializer(serializers.ModelSerializer):
    product_id = serializers.CharField(source="product.id", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku_code", read_only=True)
    business_entity_id = serializers.CharField(source="business_entity.id", read_only=True)
    business_entity_name = serializers.CharField(source="business_entity.name", read_only=True)
    marketplace_id = serializers.CharField(source="business_entity.marketplace.id", read_only=True)
    marketplace_name = serializers.CharField(
        source="business_entity.marketplace.name", read_only=True
    )

    class Meta:
        model = ProductBusinessEntity
        fields = [
            "id",
            "product_id",
            "product_name",
            "product_sku",
            "business_entity_id",
            "business_entity_name",
            "marketplace_id",
            "marketplace_name",
            "cdate",
        ]
        read_only_fields = fields
