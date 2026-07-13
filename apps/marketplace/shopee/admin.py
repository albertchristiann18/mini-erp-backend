from django.contrib import admin

from apps.marketplace.shopee.models import (
    ShopeeShop,
    ShopeeStockSyncLog,
    ShopeeSyncLog,
    ShopeeWebhookLog,
)


@admin.register(ShopeeShop)
class ShopeeShopAdmin(admin.ModelAdmin):
    list_display = ["shop_name", "shop_id", "partner_id", "is_active", "is_sandbox", "cdate"]
    list_filter = ["is_active", "is_sandbox"]
    search_fields = ["shop_name", "shop_id"]
    readonly_fields = ["access_token", "refresh_token", "cdate", "udate"]


@admin.register(ShopeeWebhookLog)
class ShopeeWebhookLogAdmin(admin.ModelAdmin):
    list_display = ["id", "shop_id", "event_code", "processed", "cdate"]
    list_filter = ["event_code", "processed"]
    search_fields = ["shop_id"]
    readonly_fields = ["payload", "signature", "cdate", "udate"]


@admin.register(ShopeeSyncLog)
class ShopeeSyncLogAdmin(admin.ModelAdmin):
    list_display = ["id", "shop", "sync_type", "status", "records_synced", "started_at"]
    list_filter = ["sync_type", "status"]
    readonly_fields = ["started_at", "finished_at", "cdate", "udate"]


@admin.register(ShopeeStockSyncLog)
class ShopeeStockSyncLogAdmin(admin.ModelAdmin):
    list_display = [
        "sku_variant_code",
        "shop",
        "quantity_synced",
        "success",
        "sync_type",
        "cdate",
    ]
    list_filter = ["success", "sync_type", "shop"]
    search_fields = ["sku_variant_code"]
    readonly_fields = ["shopee_response", "cdate", "udate"]
    ordering = ["-cdate"]
