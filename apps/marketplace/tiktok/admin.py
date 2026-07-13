from django.contrib import admin

from apps.marketplace.tiktok.models import TikTokShop, TikTokSyncLog, TikTokWebhookLog


@admin.register(TikTokShop)
class TikTokShopAdmin(admin.ModelAdmin):
    list_display = ["shop_name", "shop_id", "app_key", "is_active", "cdate"]
    list_filter = ["is_active"]
    search_fields = ["shop_name", "shop_id"]
    readonly_fields = ["access_token", "refresh_token", "cdate", "udate"]


@admin.register(TikTokWebhookLog)
class TikTokWebhookLogAdmin(admin.ModelAdmin):
    list_display = ["id", "shop", "event_type", "processed", "cdate"]
    list_filter = ["event_type", "processed"]
    readonly_fields = ["payload", "cdate", "udate"]


@admin.register(TikTokSyncLog)
class TikTokSyncLogAdmin(admin.ModelAdmin):
    list_display = ["id", "shop", "sync_type", "status", "orders_synced", "started_at"]
    list_filter = ["sync_type", "status"]
    readonly_fields = ["started_at", "finished_at", "cdate", "udate"]
