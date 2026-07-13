from django.db import models
from django_ulid.models import ULIDField

from core.models import DefaultModel
from core.utils import generate_ulid


class SalesOrder(DefaultModel):
    class OrderStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        CONFIRMED = "CONFIRMED", "Confirmed"
        SHIPPING = "SHIPPING", "Shipping"
        DELIVERED = "DELIVERED", "Delivered"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"
        RETURNED = "RETURNED", "Returned"

    class SourcePlatform(models.TextChoices):
        SHOPEE = "SHOPEE", "Shopee"
        TIKTOK = "TIKTOK", "TikTok"
        MANUAL = "MANUAL", "Manual"

    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="sales_order_id"
    )
    order_number = models.CharField(max_length=100, unique=True, editable=False, default="")
    marketplace = models.ForeignKey(
        "marketplace.Marketplace", on_delete=models.SET_NULL, null=True, blank=True
    )
    marketplace_order_id = models.CharField(max_length=255, db_index=True, blank=True, default="")
    marketplace_order_number = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=20, choices=OrderStatus.choices, default=OrderStatus.PENDING
    )
    source_platform = models.CharField(
        max_length=10,
        choices=SourcePlatform.choices,
        default=SourcePlatform.MANUAL,
    )
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT)
    customer_name = models.CharField(max_length=255, blank=True, default="")
    customer_phone = models.CharField(max_length=50, blank=True, default="")
    shipping_address = models.TextField(blank=True, default="")
    shipping_province = models.CharField(max_length=100, blank=True, default="")
    shipping_city = models.CharField(max_length=100, blank=True, default="")
    order_date = models.DateTimeField()
    confirmed_date = models.DateTimeField(null=True, blank=True)
    shipped_date = models.DateTimeField(null=True, blank=True)
    delivered_date = models.DateTimeField(null=True, blank=True)
    completed_date = models.DateTimeField(null=True, blank=True)
    courier_name = models.CharField(max_length=100, blank=True, default="")
    tracking_number = models.CharField(max_length=255, blank=True, default="")
    shipping_fee = models.BigIntegerField(default=0)
    shipping_fee_seller = models.BigIntegerField(default=0)
    subtotal = models.BigIntegerField(default=0)
    total_discount = models.BigIntegerField(default=0)
    total_marketplace_fee = models.BigIntegerField(default=0)
    total_cogs = models.BigIntegerField(default=0)
    net_revenue = models.BigIntegerField(default=0)
    gross_profit = models.BigIntegerField(default=0)
    note = models.TextField(blank=True, default="")

    def __str__(self) -> str:
        return self.order_number


class SalesOrderItem(DefaultModel):
    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="sales_order_item_id"
    )
    sales_order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name="items")
    product_variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT)
    quantity = models.IntegerField()
    selling_price = models.BigIntegerField()
    discount_amount = models.BigIntegerField(default=0)
    commission_fee = models.BigIntegerField(default=0)
    service_fee = models.BigIntegerField(default=0)
    total_marketplace_fee = models.BigIntegerField(default=0)
    actual_cogs_per_unit = models.BigIntegerField(default=0)
    actual_cogs_total = models.BigIntegerField(default=0)
    line_total = models.BigIntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.sales_order.order_number} - {self.product_variant}"


class SalesOrderCogsDetail(DefaultModel):
    """Records which ProductCogs FIFO layers were consumed per sales order item."""

    id = ULIDField(
        primary_key=True,
        default=generate_ulid,
        editable=False,
        db_column="sales_order_cogs_detail_id",
    )
    sales_order_item = models.ForeignKey(
        SalesOrderItem, on_delete=models.CASCADE, related_name="cogs_details"
    )
    product_cogs = models.ForeignKey("inventory.ProductCogs", on_delete=models.PROTECT)
    quantity_consumed = models.IntegerField()
    cogs_per_unit = models.BigIntegerField()
    total_cogs = models.BigIntegerField()

    def __str__(self) -> str:
        return f"COGS Detail for {self.sales_order_item}"


class SalesReturn(DefaultModel):
    class ReturnStatus(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested"
        APPROVED = "APPROVED", "Approved"
        RECEIVED = "RECEIVED", "Stock Returned"
        REJECTED = "REJECTED", "Rejected"

    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="sales_return_id"
    )
    return_number = models.CharField(max_length=100, unique=True, editable=False, default="")
    sales_order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name="returns")
    status = models.CharField(
        max_length=20, choices=ReturnStatus.choices, default=ReturnStatus.REQUESTED
    )
    reason = models.TextField(blank=True, default="")
    return_date = models.DateTimeField(null=True, blank=True)
    refund_amount = models.BigIntegerField(default=0)
    note = models.TextField(blank=True, default="")

    def __str__(self) -> str:
        return self.return_number


class SalesReturnItem(DefaultModel):
    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="sales_return_item_id"
    )
    sales_return = models.ForeignKey(SalesReturn, on_delete=models.CASCADE, related_name="items")
    sales_order_item = models.ForeignKey(SalesOrderItem, on_delete=models.PROTECT)
    product_variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT)
    quantity = models.IntegerField()
    reversed_cogs_total = models.BigIntegerField(default=0)

    def __str__(self) -> str:
        return f"Return Item for {self.sales_return.return_number}"
