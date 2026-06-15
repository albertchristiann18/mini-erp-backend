from typing import Any

from rest_framework import serializers

from apps.inventory.models import (
    BusinessEntity,
    Category,
    CompanyMarketplace,
    Product,
    ProductBusinessEntity,
    ProductPhoto,
    ProductSupplier,
    ProductVariant,
    ProductVariantMarketplace,
    StockMovement,
    Supplier,
    Warehouse,
)
from core.models import Company


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
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = [
            "id",
            "name",
            "sku_variant_code",
            "variant_values",
            "base_price",
            "current_cogs",
            "total_available_qty",
            "total_incoming_qty",
            "marketplace_listings",
            "is_active",
            "photo_url",
        ]

    def get_photo_url(self, obj: ProductVariant) -> str | None:
        if obj.photo:
            return obj.photo.url  # type: ignore[no-any-return]
        return None


class ProductSerializer(serializers.ModelSerializer):
    company_id = serializers.UUIDField(source="company.id", read_only=True)
    category_id = serializers.UUIDField(source="category.id", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    master_category_key = serializers.CharField(
        source="category.master_category_key", read_only=True
    )

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
            "master_category_key",
            "variants",
            "photos",
        ]


class SaveVariantItemSerializer(serializers.Serializer):
    id = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    variant_values = serializers.DictField(
        child=serializers.CharField(allow_blank=True), default=dict
    )
    sku_variant_code = serializers.CharField(required=False, allow_blank=True, default="")
    base_price = serializers.IntegerField(min_value=0, default=0)


class SaveVariantsSerializer(serializers.Serializer):
    variant_options = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()), default=dict
    )
    variants = SaveVariantItemSerializer(many=True)


class ProductCreateSerializer(serializers.ModelSerializer):
    company_id = serializers.CharField(write_only=True)
    category_id = serializers.CharField(write_only=True)
    master_category_key = serializers.CharField(
        source="category.master_category_key", read_only=True
    )
    variant_options = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()), required=False, default=dict
    )
    variants = SaveVariantItemSerializer(many=True, required=False, default=list)
    description = serializers.CharField(required=True, min_length=25, max_length=5000)
    supplier_id = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=None, write_only=True
    )
    supplier_link = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=None, write_only=True
    )

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
            "master_category_key",
            "variants",
            "supplier_id",
            "supplier_link",
        ]

    def validate_supplier_id(self, value: str | None) -> str | None:
        if not value:
            return None
        from apps.inventory.models import Supplier

        if not Supplier.objects.filter(id=value).exists():
            raise serializers.ValidationError("Supplier not found")
        return value

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
    product_supplier_link = serializers.SerializerMethodField()
    product_photo_url = serializers.SerializerMethodField()

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
            "product_supplier_link",
            "product_photo_url",
            "is_active",
        ]

    def get_product_supplier_link(self, obj: ProductVariant) -> str | None:
        req = self.context.get("request")
        params = getattr(req, "query_params", None) or getattr(req, "GET", {})
        supplier_id = params.get("supplier_id")
        qs = obj.product.product_suppliers
        if supplier_id:
            ps = qs.filter(supplier_id=supplier_id).first()
        else:
            ps = qs.first()
        return ps.supplier_link if ps else None

    def get_product_photo_url(self, obj: ProductVariant) -> str | None:
        if obj.photo:
            return obj.photo.url  # type: ignore[no-any-return]
        if obj.product.product_photo:
            return obj.product.product_photo.url  # type: ignore[no-any-return]
        return None

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
