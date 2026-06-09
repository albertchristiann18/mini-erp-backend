from typing import Any

from rest_framework import serializers

from apps.inventory.models import (
    Category,
    Product,
    ProductPhoto,
    ProductVariant,
    ProductVariantMarketplace,
    ProductVariantSupplier,
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

    class Meta:
        model = ProductVariant
        fields = [
            "id",
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
            "supplier_link",
            "master_category_key",
            "variants",
            "photos",
        ]


class VariantOptionValueSerializer(serializers.Serializer):
    id = serializers.CharField()
    label = serializers.CharField()


class VariantOptionSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    order = serializers.IntegerField(min_value=1)
    values = VariantOptionValueSerializer(many=True, default=list)


class SaveVariantItemSerializer(serializers.Serializer):
    id = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    variant_values = serializers.DictField(
        child=serializers.CharField(allow_blank=True), default=dict
    )
    sku_variant_code = serializers.CharField(required=False, allow_blank=True, default="")
    base_price = serializers.IntegerField(min_value=0, default=0)


class SaveVariantsSerializer(serializers.Serializer):
    variant_options = VariantOptionSerializer(many=True, default=list)
    variants = SaveVariantItemSerializer(many=True)


class ProductCreateSerializer(serializers.ModelSerializer):
    company_id = serializers.CharField(write_only=True)
    category_id = serializers.CharField(write_only=True)
    master_category_key = serializers.CharField(
        source="category.master_category_key", read_only=True
    )
    variant_options = VariantOptionSerializer(many=True, required=False, default=list)
    variants = SaveVariantItemSerializer(many=True, required=False, default=list)
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
            "master_category_key",
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
    product_supplier_link = serializers.CharField(
        source="product.supplier_link", read_only=True, allow_null=True
    )
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

    def get_product_photo_url(self, obj: ProductVariant) -> str | None:
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


class ProductVariantSupplierSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    product_variant_id = serializers.CharField(write_only=True)
    supplier_id = serializers.CharField(write_only=True)

    class Meta:
        model = ProductVariantSupplier
        fields = [
            "id",
            "product_variant_id",
            "supplier_id",
            "supplier_name",
            "supplier_link",
            "is_primary",
            "notes",
            "cdate",
            "udate",
        ]
        read_only_fields = ["id", "supplier_name", "cdate", "udate"]

    def create(self, validated_data: dict) -> ProductVariantSupplier:
        from apps.inventory.models import ProductVariant, Supplier

        product_variant = ProductVariant.objects.get(id=validated_data.pop("product_variant_id"))
        supplier = Supplier.objects.get(id=validated_data.pop("supplier_id"))
        return ProductVariantSupplier.objects.create(
            product_variant=product_variant,
            supplier=supplier,
            company=product_variant.company,
            **validated_data,
        )

    def update(
        self, instance: ProductVariantSupplier, validated_data: dict
    ) -> ProductVariantSupplier:
        validated_data.pop("product_variant_id", None)
        validated_data.pop("supplier_id", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance
