from rest_framework import serializers

from apps.marketplace.tiktok.models import TikTokShop, TikTokSyncLog, TikTokWebhookLog


class TikTokShopSerializer(serializers.ModelSerializer):
    is_token_expired = serializers.SerializerMethodField()

    class Meta:
        model = TikTokShop
        fields = [
            "id",
            "shop_id",
            "shop_name",
            "app_key",
            "app_secret",
            "is_active",
            "warehouse",
            "access_token",
            "token_expires_at",
            "is_token_expired",
            "cdate",
            "udate",
        ]
        extra_kwargs = {
            "app_secret": {"write_only": True},
            "access_token": {"read_only": True},
        }

    def get_is_token_expired(self, obj: TikTokShop) -> bool:
        return obj.is_token_expired()


class TikTokWebhookLogSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    shop = serializers.CharField(source="shop_id", read_only=True)

    class Meta:
        model = TikTokWebhookLog
        fields = [
            "id",
            "shop",
            "event_type",
            "payload",
            "processed",
            "error",
            "cdate",
        ]


class TikTokSyncLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = TikTokSyncLog
        fields = [
            "id",
            "shop",
            "sync_type",
            "status",
            "orders_synced",
            "message",
            "started_at",
            "finished_at",
        ]
