from django.db import models
from django_ulid.models import ULIDField

from apps.catalog.models import ProductVariant
from core.models import DefaultModel
from core.utils import generate_ulid


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
    # 3 decimal places here vs. 2 on cogs_amount/allocated_*_fee below is deliberate,
    # not an oversight: this field is a multiplicand (it's multiplied into price_rmb to
    # derive unit_price_idr), so truncating/rounding it too coarsely amplifies error
    # across every unit it prices. The money fields below are payable IDR amounts —
    # rupiah has no circulating subunit, so 2dp is already exact for them. Do not
    # unify the two precisions.
    exchange_rate = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        help_text="Exchange rate from PO at time of delivery (matches PurchaseOrder.exchange_rate precision)",
    )
    cogs_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        help_text="Unit price in IDR = price_rmb * exchange_rate",
    )
    allocated_shipping_fee = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=0,
        help_text="Shipping fee allocated per unit (IDR)",
    )
    allocated_delivery_fee = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=0,
        help_text="Delivery fee allocated per unit (IDR)",
    )
    allocated_commission_fee = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=0,
        help_text="Commission fee allocated per item total (IDR)",
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
