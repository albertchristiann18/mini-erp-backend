from django.db import models
from django_ulid.models import ULIDField

from core.models import DefaultModel, TimeStampedModel
from core.utils import generate_ulid


class ShopeeShop(DefaultModel):
    """Credentials and state for one connected Shopee shop."""

    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="shopee_shop_id"
    )
    shop_id = models.BigIntegerField(unique=True, help_text="Shopee shop_id")
    shop_name = models.CharField(max_length=255, blank=True, default="")
    partner_id = models.BigIntegerField(help_text="Shopee partner_id from App Console")
    partner_key = models.CharField(max_length=512, help_text="Shopee partner_key (keep secret)")

    # OAuth tokens
    access_token = models.CharField(max_length=512, blank=True, default="")
    refresh_token = models.CharField(max_length=512, blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)

    # Config
    is_sandbox = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    # Link to our Marketplace record
    marketplace = models.ForeignKey(
        "core.Marketplace",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shopee_shops",
    )

    # Default warehouse for orders from this shop
    default_warehouse = models.ForeignKey(
        "inventory.Warehouse", on_delete=models.SET_NULL, null=True, blank=True
    )

    # Sync state
    last_order_sync_at = models.DateTimeField(null=True, blank=True)
    last_product_sync_at = models.DateTimeField(null=True, blank=True)
    last_stock_sync_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "shopee_shop"

    def __str__(self) -> str:
        return f"{self.shop_name} ({self.shop_id})"

    @property
    def base_url(self) -> str:
        if self.is_sandbox:
            return "https://openplatform.sandbox.test-stable.shopee.sg"
        return "https://partner.shopeemobile.com"

    def is_token_expired(self) -> bool:
        from django.utils import timezone

        if not self.token_expires_at:
            return True
        return timezone.now() >= self.token_expires_at


class ShopeeWebhookLog(TimeStampedModel):
    """Raw webhook payloads from Shopee for audit and replay."""

    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="webhook_log_id"
    )
    shop_id = models.BigIntegerField(db_index=True)
    event_code = models.IntegerField(
        db_index=True, help_text="Shopee webhook code (3=order, 4=item, etc.)"
    )
    payload = models.JSONField()
    signature = models.CharField(max_length=512, blank=True, default="")
    processed = models.BooleanField(default=False, db_index=True)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        db_table = "shopee_webhook_log"
        ordering = ["-cdate"]

    def __str__(self) -> str:
        return f"Webhook {self.event_code} shop={self.shop_id} at {self.cdate}"


class ShopeeSyncLog(TimeStampedModel):
    """Tracks sync job runs."""

    id = ULIDField(primary_key=True, default=generate_ulid, editable=False, db_column="sync_log_id")
    shop = models.ForeignKey(ShopeeShop, on_delete=models.CASCADE, related_name="sync_logs")
    sync_type = models.CharField(max_length=50)  # 'orders', 'products', 'stock', 'finance'
    status = models.CharField(max_length=20, default="running")  # running, success, failed
    records_synced = models.IntegerField(default=0)
    error_message = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "shopee_sync_log"
        ordering = ["-started_at"]


class ShopeeStockSyncLog(DefaultModel):
    """
    Audit log for every stock sync attempt pushed to Shopee.
    One row per variant per sync attempt.
    DefaultModel provides: id (ULID), company (FK), cdate, udate.
    """

    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="stock_sync_log_id"
    )

    class SyncType(models.TextChoices):
        FULL = "FULL", "Full Sync"
        SINGLE = "SINGLE", "Single Variant"

    shop = models.ForeignKey(
        ShopeeShop,
        on_delete=models.CASCADE,
        related_name="stock_sync_logs",
    )
    variant = models.ForeignKey(
        "catalog.ProductVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shopee_sync_logs",
    )
    sku_variant_code = models.CharField(max_length=100)
    quantity_synced = models.IntegerField()
    success = models.BooleanField(default=False)
    error_message = models.TextField(blank=True, default="")
    sync_type = models.CharField(
        max_length=10,
        choices=SyncType.choices,
        default=SyncType.SINGLE,
    )
    shopee_item_id = models.BigIntegerField(null=True, blank=True)
    shopee_model_id = models.BigIntegerField(null=True, blank=True)
    shopee_response = models.JSONField(default=dict)

    class Meta:
        ordering = ["-cdate"]
        indexes = [
            models.Index(fields=["shop", "cdate"]),
            models.Index(fields=["variant", "success"]),
        ]

    def __str__(self) -> str:
        status = "OK" if self.success else "FAIL"
        return f"[{status}] {self.sku_variant_code} qty={self.quantity_synced}"
