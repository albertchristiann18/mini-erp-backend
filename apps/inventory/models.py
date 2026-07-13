from django.db import models
from django_ulid.models import ULIDField

from apps.catalog.models import (
    Product,
    ProductVariant as CatalogProductVariant,
)
from core.models import DefaultModel
from core.utils import generate_ulid


class ProductVariant(CatalogProductVariant):
    """Proxy model for migration compatibility - ProductVariant is owned by catalog app."""

    class Meta:
        proxy = True
        # Register under inventory app for old migrations that reference it
        # This allows 'inventory.ProductVariant' to be resolved during migration


class Warehouse(DefaultModel):
    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="warehouse_id"
    )

    name = models.CharField(max_length=255)
    address = models.TextField(blank=True, null=True)
    is_marketplace_visible = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "master_warehouse"


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


class StockMovement(DefaultModel):
    class MovementType(models.TextChoices):
        PURCHASE = "PUR", "Purchase Order"
        INBOUND = "IN", "Inbound (Purchase/Restock)"
        OUTBOUND = "OUT", "Outbound (Sales)"
        ADJUSTMENT = "ADJ", "Stock Adjustment (Manual)"
        TRANSFER = "TRF", "Warehouse Transfer"
        RETURN = "RET", "Customer Return"
        MARKETPLACE_SYNC = "MPS", "Marketplace Stock Reconciliation"

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


class CompanyMarketplace(DefaultModel):
    """Company-scoped sales channel. Each company owns their own list."""

    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="company_marketplace_id"
    )
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
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


class ProductDimensionImage(DefaultModel):
    """Per-dimension-value image for a product (e.g. Warna=White → image)."""

    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="product_dim_image_id"
    )
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="dimension_images")
    dim_key = models.CharField(max_length=100)
    dim_value = models.CharField(max_length=100)
    photo = models.FileField(upload_to="variants/dimension_images/")

    class Meta:
        unique_together = [["product", "dim_key", "dim_value"]]

    def __str__(self) -> str:
        return f"{self.product.sku_code} — {self.dim_key}={self.dim_value}"
