from rest_framework import serializers

from apps.marketplace.models import (
    BusinessEntity,
    CompanyMarketplace,
    Marketplace,
    MarketplaceConnection,
    ProductBusinessEntity,
)


class MarketplaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Marketplace
        fields = "__all__"


class MarketplaceConnectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MarketplaceConnection
        fields = [
            "id",
            "company",
            "platform",
            "display_name",
            "is_active",
            "shopee_shop_id",
            "tiktok_shop_id",
            "cdate",
            "udate",
        ]
        read_only_fields = ["company"]


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
