from decimal import Decimal

from django.conf import settings
from django.db import models
from django_ulid.models import ULIDField

from apps.inventory.models import ProductVariant, Warehouse
from core.models import DefaultModel
from core.utils import generate_ulid, round_decimal

# Create your models here.


class PurchaseOrder(DefaultModel):
    """
    Purchase Order for restocking inventory.
    """

    class POStatus(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ORDERED = "ORDERED", "Ordered"
        SHIPPED = "SHIPPED", "In Transit"
        DELIVERED = "DELIVERED", "Delivered at Warehouse"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="purchase_order_id"
    )
    # PO Number auto-generated per-company format: PO-{YYYY}-{SEQUENCE} (e.g., PO-2026-001)
    # Each company has its own independent sequence counter via po_number_counter table
    purchase_order_number = models.CharField(
        max_length=100,
    )
    status = models.CharField(max_length=50, choices=POStatus.choices, default=POStatus.DRAFT)
    invoice_number = models.CharField(max_length=100, blank=True, null=True)
    invoice_date = models.DateField(null=True, blank=True)
    delivery_order_number = models.CharField(max_length=100, blank=True, null=True)
    delivery_date = models.DateField(null=True, blank=True)  # latest delivery date
    forecast_delivery_date = models.DateField(null=True, blank=True)
    forecast_cbm = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    forecast_shipping_fee = models.BigIntegerField(null=True, blank=True)  # IDR
    forecast_shipping_fee_per_cbm = models.BigIntegerField(null=True, blank=True)  # IDR per m³
    commission_fee_rmb = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True
    )  # RMB
    note = models.TextField(blank=True, null=True)
    has_discount = models.BooleanField(default=False)

    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.CASCADE
    )  # delivered to which warehouse
    supplier_name = models.CharField(max_length=255, blank=True, null=True)
    supplier = models.ForeignKey(
        "inventory.Supplier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_orders",
    )

    forwarder_name = models.CharField(max_length=255, blank=True, null=True)
    shop_services = models.CharField(max_length=255, blank=True, null=True)  # jasa belanja
    commission_fee_pct = models.IntegerField(default=0, blank=True, null=True)
    commission_fee = models.BigIntegerField(blank=True, null=True)  # IDR

    delivery_fee = models.DecimalField(
        max_digits=10, decimal_places=3, blank=True, null=True
    )  # RMB / else, from china supplier to china warehouse
    currency = models.CharField(max_length=10, blank=True, null=True)  # RMB, USD, etc
    exchange_rate = models.DecimalField(
        max_digits=10, decimal_places=3, blank=True, null=True
    )  # IDR

    # The file will be uploaded to R2 automatically because of our settings.py
    purchase_order_invoice_file = models.FileField(upload_to="po/invoices/", null=True, blank=True)
    delivery_order_file = models.FileField(upload_to="po/delivery_orders/", null=True, blank=True)
    delivery_order_invoice_file = models.FileField(
        upload_to="po/delivery_invoices/", null=True, blank=True
    )
    packing_list_file = models.FileField(upload_to="po/packing_lists/", null=True, blank=True)

    total_ordered_qty = models.IntegerField(default=0)
    total_received_qty = models.IntegerField(default=0)
    cbm = models.DecimalField(max_digits=10, decimal_places=3, blank=True, null=True)  # CBM
    weight = models.DecimalField(max_digits=10, decimal_places=3, blank=True, null=True)  # kg

    shipping_fee_per_cbm = models.BigIntegerField(default=0, blank=True, null=True)  # IDR
    shipping_fee = models.BigIntegerField(default=0, blank=True, null=True)  # IDR
    procure_amount = models.BigIntegerField(blank=True, null=True)  # IDR
    refund_amount = models.BigIntegerField(blank=True, null=True)  # IDR
    cogs_ratio_forecast = models.DecimalField(
        max_digits=6, decimal_places=2, blank=True, null=True
    )  # Forecast COGS overhead ratio %, e.g. 15.00 means 15%
    total_item_amount = models.BigIntegerField(blank=True, null=True)  # IDR
    total_order_amount = models.BigIntegerField(blank=True, null=True)  # IDR
    total_amount = models.BigIntegerField(blank=True, null=True)  # IDR

    def __str__(self) -> str:
        return self.purchase_order_number

    STATUS_TRANSITIONS: dict[str, list[str]] = {
        POStatus.DRAFT: [POStatus.ORDERED],
        POStatus.ORDERED: [POStatus.SHIPPED],
        POStatus.SHIPPED: [POStatus.DELIVERED],
        POStatus.DELIVERED: [POStatus.COMPLETED],
        POStatus.COMPLETED: [],
        POStatus.CANCELLED: [],
    }

    _LOCKED_FROM_SHIPPED: frozenset[str] = frozenset(
        {
            "exchange_rate",
            "purchase_order_invoice_file",
            "invoice_number",
            "commission_fee_pct",
            "delivery_fee",
        }
    )

    _LOCKED_FROM_DELIVERED: frozenset[str] = _LOCKED_FROM_SHIPPED | frozenset(
        {
            "delivery_order_number",
            "cbm",
            "delivery_order_file",
        }
    )

    _ALL_EDITABLE_HEADER: list[str] = [
        "supplier_name",
        "forwarder_name",
        "shop_services",
        "currency",
        "exchange_rate",
        "commission_fee_pct",
        "delivery_fee",
        "commission_fee_rmb",
        "invoice_number",
        "invoice_date",
        "delivery_order_number",
        "delivery_date",
        "forecast_delivery_date",
        "cbm",
        "forecast_cbm",
        "weight",
        "shipping_fee_per_cbm",
        "forecast_shipping_fee_per_cbm",
        "purchase_order_invoice_file",
        "delivery_order_file",
        "delivery_order_invoice_file",
        "packing_list_file",
        "note",
        "has_discount",
    ]

    _ALL_EDITABLE_ORDER_DETAIL: list[str] = [
        "ordered_qty",
        "unit_price_foreign",
        "discounted_unit_price_foreign",
    ]

    @classmethod
    def get_editable_fields(cls, status: str) -> dict[str, list[str]]:
        if status == cls.POStatus.DRAFT:
            return {
                "header": list(cls._ALL_EDITABLE_HEADER),
                "order_detail": list(cls._ALL_EDITABLE_ORDER_DETAIL),
            }
        elif status == cls.POStatus.ORDERED:
            return {
                "header": list(cls._ALL_EDITABLE_HEADER),
                "order_detail": ["ordered_qty"],
            }
        elif status == cls.POStatus.SHIPPED:
            return {
                "header": [
                    f for f in cls._ALL_EDITABLE_HEADER if f not in cls._LOCKED_FROM_SHIPPED
                ],
                "order_detail": [],
            }
        elif status == cls.POStatus.DELIVERED:
            return {
                "header": [
                    f for f in cls._ALL_EDITABLE_HEADER if f not in cls._LOCKED_FROM_DELIVERED
                ],
                "order_detail": ["received_qty", "remarks"],
            }
        else:  # COMPLETED, CANCELLED
            return {"header": ["note", "has_discount"], "order_detail": []}

    def get_next_status(self) -> str | None:
        transitions = self.STATUS_TRANSITIONS.get(self.status, [])
        return transitions[0] if transitions else None

    def can_advance_to(self, new_status: str) -> bool:
        return new_status in self.STATUS_TRANSITIONS.get(self.status, [])

    def get_shipping_per_qty(self) -> int:
        if not self.shipping_fee or not self.total_ordered_qty:
            return 0

        return int(round(self.shipping_fee / self.total_ordered_qty))

    def cost_ratio_cogs(self) -> Decimal:
        if self.total_item_amount and self.total_item_amount > 0:
            delivery_fee_idr = Decimal(str(self.delivery_fee or 0)) * Decimal(
                str(self.exchange_rate or 0)
            )
            commission = Decimal(str(self.commission_fee or 0))
            shipping = Decimal(str(self.shipping_fee or 0))
            val = (
                (delivery_fee_idr + commission + shipping)
                / Decimal(str(self.total_item_amount))
                * 100
            )
            return round_decimal(val)
        return Decimal("0.0")


class PurchaseOrderDetail(DefaultModel):
    """Details of each item in a Purchase Order"""

    id = ULIDField(
        primary_key=True,
        default=generate_ulid,
        editable=False,
        db_column="purchase_order_detail_id",
    )
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="order_details"
    )
    product_variant = models.ForeignKey(
        ProductVariant, on_delete=models.CASCADE, null=True, blank=True
    )
    sourcing_item = models.ForeignKey(
        "purchasing.SourcingPoolItem",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="po_details",
    )
    draft_product_name = models.CharField(max_length=255, blank=True, default="")

    ordered_qty = models.IntegerField(default=0)
    received_qty = models.IntegerField(default=0)
    updated_qty = models.IntegerField(default=0)  # for tracking changes in received qty

    received_date = models.DateField(null=True, blank=True)
    remarks = models.TextField(blank=True, null=True)

    unit_price_foreign = models.DecimalField(
        max_digits=15, decimal_places=3, blank=True, null=True
    )  # RMB / else
    unit_price_base = models.BigIntegerField(blank=True, null=True)  # IDR
    total_price_foreign = models.DecimalField(
        max_digits=15, decimal_places=3, blank=True, null=True
    )  # RMB / else
    total_price_base = models.BigIntegerField(blank=True, null=True)  # IDR

    discounted_unit_price_foreign = models.DecimalField(
        max_digits=15, decimal_places=3, blank=True, null=True
    )  # RMB / else
    discounted_unit_price_base = models.BigIntegerField(blank=True, null=True)  # IDR
    discounted_total_price_foreign = models.DecimalField(
        max_digits=15, decimal_places=3, blank=True, null=True
    )  # RMB / else
    discounted_total_price_base = models.BigIntegerField(blank=True, null=True)  # IDR

    incoming_qty = models.IntegerField(
        default=0
    )  # Stock incoming from other PO when the PO created
    stock_on_hand = models.IntegerField(default=0)  # Stock before PO received
    avg_sales = models.DecimalField(
        max_digits=15, decimal_places=3, blank=True, null=True
    )  # Average sales per day (30-day window) at time of ORDERED
    avg_sales_7d = models.DecimalField(
        max_digits=15, decimal_places=3, blank=True, null=True
    )  # Average sales per day (7-day window) at time of ORDERED

    supplier_link = models.CharField(max_length=500, blank=True, null=True)

    def __str__(self) -> str:
        if self.product_variant_id:  # type: ignore[attr-defined]
            return f"{self.purchase_order.purchase_order_number} - {self.product_variant.sku_variant_code}"  # type: ignore[union-attr]
        return f"{self.purchase_order.purchase_order_number} - [Draft] {self.draft_product_name}"


class PurchaseOrderStatusHistory(DefaultModel):
    """Audit log of every status transition on a PurchaseOrder."""

    id = ULIDField(
        primary_key=True,
        default=generate_ulid,
        editable=False,
        db_column="purchase_order_status_history_id",
    )
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="status_history"
    )
    from_status = models.CharField(max_length=50)
    to_status = models.CharField(max_length=50)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    note = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["-cdate"]

    def __str__(self) -> str:
        return f"{self.purchase_order.purchase_order_number}: {self.from_status} → {self.to_status}"


class SourcingPool(DefaultModel):
    """One persistent pool per supplier per company. Auto-created on first import."""

    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="sourcing_pool_id"
    )
    supplier = models.ForeignKey(
        "inventory.Supplier",
        on_delete=models.CASCADE,
        related_name="sourcing_pools",
    )

    class Meta:
        unique_together = [("company", "supplier")]

    def __str__(self) -> str:
        return f"Pool — {self.supplier.name} ({self.company.name})"


class SourcingPoolItem(DefaultModel):
    """
    One candidate item (one variant) in a supplier's sourcing pool.

    image_download_status lifecycle:
      PENDING — default for all items. Either no image_url yet, or URL not yet downloaded.
                Phase 2 async downloader skips items where image_url is None.
      DONE    — image file successfully downloaded to R2 (set by Phase 2).
      FAILED  — download attempted but failed (set by Phase 2).
    """

    class ImageDownloadStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        DONE = "DONE", "Done"
        FAILED = "FAILED", "Failed"

    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="sourcing_pool_item_id"
    )
    pool = models.ForeignKey(SourcingPool, on_delete=models.CASCADE, related_name="items")

    product_name = models.CharField(max_length=255, null=True, blank=True)
    variant_name = models.CharField(max_length=255)
    category = models.ForeignKey(
        "inventory.Category",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sourcing_items",
    )
    unit_price = models.DecimalField(max_digits=15, decimal_places=3)
    discounted_price = models.DecimalField(max_digits=15, decimal_places=3, null=True, blank=True)
    qty_suggested = models.IntegerField(null=True, blank=True)
    supplier_link = models.URLField(max_length=500, blank=True, null=True)
    image_url = models.URLField(max_length=1000, blank=True, null=True)
    image_file = models.FileField(upload_to="sourcing/images/", null=True, blank=True)
    image_download_status = models.CharField(
        max_length=20,
        choices=ImageDownloadStatus.choices,
        default=ImageDownloadStatus.PENDING,
    )
    notes = models.TextField(blank=True, null=True)
    variant_code = models.CharField(max_length=100, blank=True, null=True)
    variant = models.ForeignKey(
        "inventory.ProductVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sourcing_items",
    )
    times_ordered = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["pool", "supplier_link", "variant_name"],
                condition=models.Q(supplier_link__isnull=False) & ~models.Q(supplier_link=""),
                name="unique_pool_supplier_link_variant",
            ),
            models.UniqueConstraint(
                fields=["pool", "product_name", "variant_name"],
                condition=models.Q(supplier_link__isnull=True) | models.Q(supplier_link=""),
                name="unique_pool_product_name_variant",
            ),
        ]

    def __str__(self) -> str:
        name = self.product_name or "(Unnamed)"
        return f"{self.pool.supplier.name} — {name} / {self.variant_name}"
