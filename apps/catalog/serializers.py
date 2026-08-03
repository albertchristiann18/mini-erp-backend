from typing import Any
from urllib.parse import quote

from rest_framework import serializers

from apps.catalog.models import (
    Category,
    Product,
    ProductPhoto,
    ProductVariant,
    ProductVariantMarketplace,
)
from core.models import Company


class CategorySerializer(serializers.ModelSerializer):
    company = serializers.UUIDField(source="company.id", read_only=True)

    class Meta:
        model = Category
        # '__all__' includes all fields: id, name, category_code, description, is_active
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

    dim1_key = serializers.CharField(required=False, allow_blank=True, default="")
    dim2_key = serializers.CharField(required=False, allow_blank=True, default="")
    dim1_options = serializers.JSONField(required=False, default=list)
    dim2_options = serializers.JSONField(required=False, default=list)
    dimension_images = serializers.SerializerMethodField()

    def get_dimension_images(self, obj: Product) -> list[dict]:
        request = self.context.get("request")
        results = []
        for di in obj.dimension_images.all():
            if request is not None:
                proxy_url = request.build_absolute_uri(
                    f"/product/{obj.id}/photo-proxy/"
                    f"?dim_key={quote(di.dim_key)}&dim_value={quote(di.dim_value)}"
                )
            else:
                proxy_url = di.photo.url if di.photo else None
            results.append(
                {
                    "dim_key": di.dim_key,
                    "dim_value": di.dim_value,
                    "photo_url": proxy_url,
                }
            )
        return results

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
            "dim1_key",
            "dim2_key",
            "dim1_options",
            "dim2_options",
            "dimension_images",
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
        from apps.purchasing.models import Supplier

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
            "last_unit_price_foreign",
            "last_currency",
            "last_discounted_unit_price_foreign",
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
        gallery = sorted(obj.product.photos.all(), key=lambda p: p.order)
        if gallery:
            return gallery[0].image.url  # type: ignore[no-any-return]
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
