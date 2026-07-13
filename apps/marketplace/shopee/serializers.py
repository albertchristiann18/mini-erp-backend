from rest_framework import serializers

from apps.marketplace.shopee.models import ShopeeShop, ShopeeSyncLog, ShopeeWebhookLog


class ShopeeShopSerializer(serializers.ModelSerializer):
    is_token_expired = serializers.SerializerMethodField()

    class Meta:
        model = ShopeeShop
        fields = [
            "id",
            "shop_id",
            "shop_name",
            "partner_id",
            "partner_key",
            "is_sandbox",
            "is_active",
            "marketplace",
            "default_warehouse",
            "access_token",
            "token_expires_at",
            "is_token_expired",
            "last_order_sync_at",
            "last_product_sync_at",
            "last_stock_sync_at",
            "cdate",
            "udate",
        ]
        extra_kwargs = {
            "partner_key": {"write_only": True},
            "access_token": {"read_only": True},
        }

    def get_is_token_expired(self, obj: ShopeeShop) -> bool:
        return obj.is_token_expired()


class ShopeeWebhookLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopeeWebhookLog
        fields = [
            "id",
            "shop_id",
            "event_code",
            "payload",
            "processed",
            "error_message",
            "cdate",
        ]


class ShopeeSyncLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopeeSyncLog
        fields = [
            "id",
            "shop",
            "sync_type",
            "status",
            "records_synced",
            "error_message",
            "started_at",
            "finished_at",
        ]
