from typing import Any

from rest_framework import serializers

from apps.inventory.models import (
    Category,
    Product,
    ProductPhoto,
    ProductVariant,
    ProductVariantMarketplace,
    StockMovement,
    Warehouse,
)
from core.models import Company, Marketplace


class CategorySerializer(serializers.ModelSerializer):
    company = serializers.UUIDField(source="company.id", read_only=True)

    class Meta:
        model = Category
        # '__all__' includes all fields: id, name, category_code, description, is_active
        fields = "__all__"


class WarehouseSerializer(serializers.ModelSerializer):
    company = serializers.UUIDField(source="company.id", read_only=True)

    class Meta:
        model = Warehouse
        fields = "__all__"


class ProductPhotoSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ProductPhoto
        fields = ["id", "image_url", "order", "is_primary"]

    def get_image_url(self, obj: ProductPhoto) -> Any:
        if obj.image:
            return obj.image.url
        return None


class VariantMarketplaceSerializer(serializers.ModelSerializer):
    marketplace_id = serializers.UUIDField(source="marketplace.id", read_only=True)

    class Meta:
        model = ProductVariantMarketplace
        fields = ["marketplace_id", "selling_price", "discounted_price", "is_active"]


class VariantSerializer(serializers.ModelSerializer):
    # Nest the marketplace pricing inside the variant
    marketplace_listings = VariantMarketplaceSerializer(many=True)

    class Meta:
        model = ProductVariant
        fields = [
            "name",
            "sku_variant_code",
            "variant_values",
            "base_price",
            "marketplace_listings",
            "is_active",
        ]


class ProductSerializer(serializers.ModelSerializer):
    company_id = serializers.UUIDField(source="company.id", read_only=True)
    category_id = serializers.UUIDField(source="category.id", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)

    variants = VariantSerializer(many=True, read_only=True)
    photos = ProductPhotoSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "company_id",
            "category_id",
            "category_name",
            "name",
            "description",
            "sku_code",
            "total_qty",
            "total_cogs",
            "variant_options",
            "specifications",
            "weight",
            "length",
            "width",
            "height",
            "is_active",
            "variants",
            "photos",
        ]


class VariantMarketplaceCreateSerializer(serializers.ModelSerializer):
    marketplace_id = serializers.CharField(write_only=True)

    class Meta:
        model = ProductVariantMarketplace
        fields = ["marketplace_id", "selling_price", "discounted_price"]

    def validate_marketplace_id(self, value: Any) -> Any:
        if not Marketplace.objects.filter(id=value).exists():
            raise serializers.ValidationError("Marketplace not found")
        return value


class VariantCreateSerializer(serializers.ModelSerializer):
    marketplace_listings = VariantMarketplaceCreateSerializer(many=True)

    class Meta:
        model = ProductVariant
        fields = [
            "name",
            # "sku_variant_code",
            "variant_values",
            "base_price",
            "marketplace_listings",
        ]


class ProductCreateSerializer(serializers.ModelSerializer):
    company_id = serializers.CharField(write_only=True)
    category_id = serializers.CharField(write_only=True)
    variants = VariantCreateSerializer(many=True)
    description = serializers.CharField(required=True, min_length=25, max_length=5000)

    class Meta:
        model = Product
        fields = [
            "company_id",
            "category_id",
            "name",
            "description",
            "variant_options",
            "specifications",
            "weight",
            "length",
            "width",
            "height",
            "variants",
        ]

    def validate_company_id(self, value: Any) -> Any:
        # Return the value (the ID string) exactly as it was passed.
        if not Company.objects.filter(id=value).exists():
            raise serializers.ValidationError("Company not found")
        return value

    def validate_category_id(self, value: Any) -> Any:
        if not Category.objects.filter(id=value).exists():
            raise serializers.ValidationError("Category not found")
        return value


class ProductVariantStockSerializer(serializers.ModelSerializer):
    product = serializers.CharField(source="product.id", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku_code", read_only=True)
    category_name = serializers.CharField(source="product.category.name", read_only=True)
    physical_qty = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = [
            "id",
            "name",
            "sku_variant_code",
            "product",
            "product_name",
            "product_sku",
            "category_name",
            "base_price",
            "total_available_qty",
            "physical_qty",
            "is_active",
        ]

    def get_physical_qty(self, obj: ProductVariant) -> int:
        req = self.context.get("request")
        # DRF wraps the request with .query_params; plain WSGIRequest uses .GET
        params = getattr(req, "query_params", None) or getattr(req, "GET", {})
        warehouse_id = params.get("warehouse")
        if warehouse_id:
            stock = obj.warehouse_stocks.filter(warehouse_id=warehouse_id).first()
            return stock.physical_qty if stock else 0
        from django.db.models import Sum

        result = obj.warehouse_stocks.aggregate(total=Sum("physical_qty"))
        return result["total"] or 0


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
