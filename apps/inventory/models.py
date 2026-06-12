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
        db_table = "product_photo"

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

    def __str__(self) -> str:
        return f"{self.sku_variant_code}-{self.name}"


class Warehouse(DefaultModel):
    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="warehouse_id"
    )

    name = models.CharField(max_length=255)
    address = models.TextField(blank=True, null=True)
    is_marketplace_visible = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)


class ProductVariantWarehouse(DefaultModel):
    """Stock of each variant in each warehouse"""

    id = ULIDField(
        primary_key=True,
        default=generate_ulid,
        editable=False,
        db_column="product_variant_warehouse_id",
    )
    product_variant = models.ForeignKey(
        ProductVariant, on_delete=models.CASCADE, related_name="warehouse_stocks"
    )
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.CASCADE, related_name="variant_stocks"
    )

    incoming_qty = models.IntegerField(default=0)
    outgoing_qty = models.IntegerField(default=0)
    physical_qty = models.IntegerField(default=0)
    checkout_qty = models.IntegerField(default=0)

    @property
    def available_qty(self) -> int:
        """Stock available for sale"""
        return self.physical_qty - self.checkout_qty

    class Meta:
        unique_together = ["product_variant", "warehouse"]
        indexes = [
            models.Index(fields=["product_variant", "warehouse"]),
        ]


class ProductCogs(DefaultModel):
    """FIFO inventory layers per variant per warehouse."""

    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="product_cogs_id"
    )
    product_variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE)
    reference_number = models.CharField(
        max_length=100,
        default="",
        blank=True,
        db_index=True,
        help_text="Source document reference (e.g., PO purchase_order_number)",
    )

    purchase_date = models.DateField(help_text="Date from PurchaseOrder.invoice_date")
    price_rmb = models.DecimalField(
        max_digits=15, decimal_places=4, help_text="Unit price in RMB (unit_price_foreign)"
    )
    exchange_rate = models.BigIntegerField(help_text="Exchange rate from PO (rounded integer)")
    cogs_amount = models.BigIntegerField(help_text="Unit price in IDR = price_rmb * exchange_rate")
    allocated_shipping_fee = models.BigIntegerField(
        default=0, help_text="Shipping fee allocated per unit (IDR)"
    )
    allocated_delivery_fee = models.BigIntegerField(
        default=0, help_text="Delivery fee allocated per unit (IDR)"
    )
    allocated_commission_fee = models.BigIntegerField(
        default=0, help_text="Commission fee allocated per item total (IDR)"
    )

    original_qty = models.IntegerField(
        default=0,
        help_text="Total quantity that came in from PO (accumulates with each delivery)",
    )
    remaining_qty = models.IntegerField(
        default=0,
        help_text="Current available quantity. Only decreases on actual sales/outbound.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-purchase_date"]
        indexes = [
            models.Index(fields=["product_variant", "warehouse", "reference_number"]),
        ]


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


class StockMovement(DefaultModel):
    class MovementType(models.TextChoices):
        PURCHASE = "PUR", "Purchase Order"
        INBOUND = "IN", "Inbound (Purchase/Restock)"
        OUTBOUND = "OUT", "Outbound (Sales)"
        ADJUSTMENT = "ADJ", "Stock Adjustment (Manual)"
        TRANSFER = "TRF", "Warehouse Transfer"
        RETURN = "RET", "Customer Return"

    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="stock_movement_id"
    )
    product_variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT)
    movement_type = models.CharField(max_length=3, choices=MovementType.choices)
    field_change = models.CharField(max_length=100, default="")
    quantity = models.IntegerField()
    reference_number = models.CharField(max_length=100, blank=True, null=True)
    note = models.TextField(blank=True, null=True)
    balance_before = models.IntegerField()
    balance_after = models.IntegerField()

    class Meta:
        ordering = ["-cdate"]


class Supplier(DefaultModel):
    id = ULIDField(primary_key=True, default=generate_ulid, editable=False, db_column="supplier_id")
    name = models.CharField(max_length=255)
    contact_name = models.CharField(max_length=255, blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    country = models.CharField(max_length=100, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    supplier_link = models.URLField(max_length=500, blank=True, null=True)
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name


class ProductSupplier(DefaultModel):
    id = ULIDField(primary_key=True, default=generate_ulid, editable=False, db_column="product_supplier_id")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="product_suppliers")
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="product_suppliers")
    supplier_link = models.URLField(max_length=500, blank=True, null=True, help_text="URL to this product on the supplier's site")

    class Meta:
        unique_together = ["product", "supplier"]

    def __str__(self) -> str:
        return f"{self.product} — {self.supplier}"


class BusinessEntity(DefaultModel):
    id = ULIDField(primary_key=True, default=generate_ulid, editable=False, db_column="business_entity_id")
    name = models.CharField(max_length=255)
    marketplace = models.ForeignKey(
        Marketplace,
        on_delete=models.PROTECT,
        related_name="business_entities",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("company", "name")]

    def __str__(self) -> str:
        return f"{self.name} ({self.marketplace.name})"


class ProductBusinessEntity(DefaultModel):
    id = ULIDField(primary_key=True, default=generate_ulid, editable=False, db_column="product_business_entity_id")
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="business_entities",
    )
    business_entity = models.ForeignKey(
        BusinessEntity,
        on_delete=models.CASCADE,
        related_name="product_assignments",
    )

    class Meta:
        unique_together = [("product", "business_entity")]

    def __str__(self) -> str:
        return f"{self.product.sku_code} → {self.business_entity.name}"
