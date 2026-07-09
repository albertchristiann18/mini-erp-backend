from typing import Any

from django.db import models
from django_ulid.models import ULIDField

from core.models import DefaultModel, Marketplace
from core.utils import generate_ulid, get_default_shipping_config


class Category(DefaultModel):
    """Product category"""

    id = ULIDField(primary_key=True, default=generate_ulid, editable=False, db_column="category_id")
    name = models.CharField(max_length=255, unique=True)
    category_code = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    master_category_key = models.CharField(max_length=100, blank=True, default="")
    shopee_category_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        db_table = "master_category"

    def __str__(self) -> str:
        return self.name


class Product(DefaultModel):
    """
    SKU-level summary (aggregated from all variants).
    """

    id = ULIDField(primary_key=True, default=generate_ulid, editable=False, db_column="product_id")
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="products")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    sku_code = models.CharField(
        max_length=100,
        unique=True,
        editable=False,
    )

    total_qty = models.IntegerField(default=0)
    total_cogs = models.IntegerField(default=0)

    variant_options = models.JSONField(default=list, blank=True)
    specifications = models.JSONField(default=dict, blank=True)

    product_photo = models.FileField(upload_to="products/photos/", null=True, blank=True)

    weight = models.IntegerField(default=0)
    length = models.IntegerField(default=0)
    width = models.IntegerField(default=0)
    height = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    shipping_config = models.JSONField(
        default=get_default_shipping_config,
        blank=True,
        help_text="Stores marketplace-specific shipping and insurance settings",
    )

    dim1_key = models.CharField(max_length=100, blank=True, default="")
    dim2_key = models.CharField(max_length=100, blank=True, default="")
    dim1_options = models.JSONField(default=list, blank=True)
    dim2_options = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "master_product"

    def get_shopee_reguler_shipping(self) -> Any:
        """Combines General Reguler and Shopee-specific Reguler expeditions"""
        general = self.shipping_config.get("general", {}).get("reguler", {}).get("expeditions", [])
        shopee = (
            self.shipping_config.get("marketplaces", {})
            .get("Shopee", {})
            .get("reguler", {})
            .get("expeditions", [])
        )
        return general + shopee

    @property
    def total_cogs_value(self) -> float:
        return self.total_qty * self.total_cogs

    def __str__(self) -> str:
        return f"{self.sku_code}-{self.name}"


class ProductPhoto(DefaultModel):
    """Product gallery — up to 9 photos per product."""

    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="product_photo_id"
    )
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="photos")
    image = models.FileField(upload_to="products/photos/")
    order = models.PositiveSmallIntegerField(default=0)
    is_primary = models.BooleanField(default=False)

    class Meta:
        ordering = ["order"]
        db_table = "master_productphoto"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.is_primary:
            ProductPhoto.objects.filter(product=self.product, is_primary=True).exclude(
                pk=self.pk
            ).update(is_primary=False)
        super().save(*args, **kwargs)


class ProductVariant(DefaultModel):
    """
    Variant of a product (e.g., size, color) - the actual inventory unit.
    """

    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="product_variant_id"
    )
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    name = models.CharField(max_length=255)
    sku_variant_code = models.CharField(
        max_length=100,
        unique=True,
    )

    variant_values = models.JSONField(default=dict)

    current_cogs = models.BigIntegerField(default=0)

    base_price = models.BigIntegerField(default=0)

    total_incoming_qty = models.IntegerField(default=0)
    total_outgoing_qty = models.IntegerField(default=0)
    total_available_qty = models.IntegerField(default=0)

    is_active = models.BooleanField(default=True)
    is_fake = models.BooleanField(default=False)
    photo = models.FileField(upload_to="variants/photos/", null=True, blank=True)

    last_unit_price_foreign = models.DecimalField(
        max_digits=14, decimal_places=4, null=True, blank=True
    )
    last_currency = models.CharField(max_length=10, null=True, blank=True)
    last_discounted_unit_price_foreign = models.DecimalField(
        max_digits=15, decimal_places=3, null=True, blank=True
    )
    price_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "master_productvariant"

    def __str__(self) -> str:
        return f"{self.sku_variant_code}-{self.name}"


class ProductVariantMarketplace(DefaultModel):
    """Pricing per variant per marketplace (stock is unified)"""

    id = ULIDField(
        primary_key=True,
        default=generate_ulid,
        editable=False,
        db_column="product_variant_marketplace_id",
    )
    product_variant = models.ForeignKey(
        ProductVariant, on_delete=models.CASCADE, related_name="marketplace_listings"
    )
    marketplace = models.ForeignKey(
        Marketplace, on_delete=models.CASCADE, related_name="product_listings"
    )

    selling_price = models.BigIntegerField()
    discounted_price = models.BigIntegerField(null=True, blank=True)

    shopee_item_id = models.BigIntegerField(null=True, blank=True)
    shopee_model_id = models.BigIntegerField(null=True, blank=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ["product_variant", "marketplace"]
        db_table = "master_productvariantmarketplace"
