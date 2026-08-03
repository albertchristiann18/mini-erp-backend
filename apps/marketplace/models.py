from django.db import models
from django_ulid.models import ULIDField

from core.models import DefaultModel, TimeStampedModel
from core.utils import generate_ulid, get_default_shipping_config


class Marketplace(TimeStampedModel):
    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="marketplace_id"
    )
    name = models.CharField(max_length=255)
    url = models.URLField(blank=True, null=True)
    status = models.CharField(max_length=50, blank=True, null=True)
    connected_time = models.DateTimeField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    # New Shipping Configuration field
    shipping_config = models.JSONField(
        default=get_default_shipping_config,
        blank=True,
        help_text="Stores marketplace-specific shipping and insurance settings",
    )

    class Meta:
        db_table = "core_marketplace"


class MarketplaceConnection(TimeStampedModel):
    PLATFORM_CHOICES = [
        ("SHOPEE", "Shopee"),
        ("TIKTOK", "TikTok Shop"),
    ]
    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="connection_id"
    )
    company = models.ForeignKey(
        "core.Company", on_delete=models.CASCADE, related_name="marketplace_connections"
    )
    platform = models.CharField(max_length=32, choices=PLATFORM_CHOICES)
    display_name = models.CharField(
        max_length=255, blank=True, help_text="e.g. 'Brand A Shopee Official'"
    )
    is_active = models.BooleanField(default=True)
    shopee_shop = models.OneToOneField(
        "shopee.ShopeeShop",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="connection",
    )
    tiktok_shop = models.OneToOneField(
        "tiktok.TikTokShop",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="connection",
    )

    class Meta:
        db_table = "marketplace_connection"
        unique_together = [("company", "platform", "display_name")]

    def __str__(self) -> str:
        return f"{self.company.name} - {self.platform}"


class CompanyMarketplace(DefaultModel):
    """Company-scoped sales channel. Each company owns their own list."""

    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="company_marketplace_id"
    )
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "inventory_companymarketplace"
        unique_together = [("company", "name")]

    def __str__(self) -> str:
        return f"{self.name} ({self.company.name})"


class BusinessEntity(DefaultModel):
    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="business_entity_id"
    )
    name = models.CharField(max_length=255)
    marketplace = models.ForeignKey(
        CompanyMarketplace,
        on_delete=models.PROTECT,
        related_name="business_entities",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "inventory_businessentity"
        unique_together = [("company", "name")]

    def __str__(self) -> str:
        return f"{self.name} ({self.marketplace.name})"


class ProductBusinessEntity(DefaultModel):
    id = ULIDField(
        primary_key=True,
        default=generate_ulid,
        editable=False,
        db_column="product_business_entity_id",
    )
    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.CASCADE,
        related_name="business_entities",
    )
    business_entity = models.ForeignKey(
        BusinessEntity,
        on_delete=models.CASCADE,
        related_name="product_assignments",
    )

    class Meta:
        db_table = "inventory_productbusinessentity"
        unique_together = [("product", "business_entity")]

    def __str__(self) -> str:
        return f"{self.product.sku_code} → {self.business_entity.name}"
