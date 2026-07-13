from django.db import models
from django_ulid.models import ULIDField

from core.models import DefaultModel, TimeStampedModel
from core.utils import generate_ulid


class TikTokShop(DefaultModel):
    """Connected TikTok Shop account."""

    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="tiktok_shop_id"
    )
    shop_id = models.CharField(max_length=64, unique=True, help_text="TikTok shop_id")
    shop_name = models.CharField(max_length=255, blank=True, default="")
    app_key = models.CharField(max_length=128, help_text="TikTok App Key")
    app_secret = models.CharField(max_length=255, help_text="TikTok App Secret (keep secret)")

    # OAuth tokens
    access_token = models.CharField(max_length=512, blank=True, default="")
    refresh_token = models.CharField(max_length=512, blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)

    # Config
    is_active = models.BooleanField(default=True)

    # Default warehouse for orders from this shop
    warehouse = models.ForeignKey(
        "inventory.Warehouse", on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        db_table = "tiktok_shop"

    def __str__(self) -> str:
        return f"{self.shop_name} ({self.shop_id})"

    def is_token_expired(self) -> bool:
        from django.utils import timezone

        if not self.token_expires_at:
            return True
        return timezone.now() >= self.token_expires_at


class TikTokWebhookLog(TimeStampedModel):
    """Raw webhook payload log from TikTok for audit and replay."""

    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="webhook_log_id"
    )
    shop = models.ForeignKey(
        TikTokShop, on_delete=models.CASCADE, related_name="webhook_logs", null=True, blank=True
    )
    event_type = models.CharField(max_length=64, db_index=True)
    payload = models.JSONField()
    processed = models.BooleanField(default=False, db_index=True)
    error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "tiktok_webhook_log"
        ordering = ["-cdate"]

    def __str__(self) -> str:
        shop_id = self.shop.pk if self.shop else None
        return f"Webhook {self.event_type} shop={shop_id} at {self.cdate}"


class TikTokSyncLog(TimeStampedModel):
    """Sync operation log."""

    id = ULIDField(primary_key=True, default=generate_ulid, editable=False, db_column="sync_log_id")
    shop = models.ForeignKey(TikTokShop, on_delete=models.CASCADE, related_name="sync_logs")
    sync_type = models.CharField(max_length=32)  # "orders" or "stock"
    status = models.CharField(max_length=16, default="running")  # "running", "success", "error"
    message = models.TextField(blank=True, default="")
    orders_synced = models.IntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tiktok_sync_log"
        ordering = ["-started_at"]
