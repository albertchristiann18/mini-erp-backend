# mypy: disable-error-code="no-untyped-call"
import factory

from apps.marketplace.tiktok.models import TikTokShop, TikTokSyncLog, TikTokWebhookLog
from core.factories import CompanyFactory


class TikTokShopFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TikTokShop

    company = factory.SubFactory(CompanyFactory)
    shop_id = factory.Sequence(lambda n: f"tiktok_{1000000 + n}")
    shop_name = factory.Sequence(lambda n: f"TikTok Shop {n}")
    app_key = "test_app_key"
    app_secret = "test_app_secret_abc123"
    access_token = "test_access_token"
    refresh_token = "test_refresh_token"
    is_active = True


class TikTokWebhookLogFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TikTokWebhookLog

    shop = factory.SubFactory(TikTokShopFactory)
    event_type = "order.status_update"
    payload = factory.LazyAttribute(
        lambda o: {
            "order_id": "TEST_ORDER_123",
            "status": "AWAITING_SHIPMENT",
            "shop_id": o.shop.shop_id if o.shop else "",
        }
    )
    processed = False


class TikTokSyncLogFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TikTokSyncLog

    shop = factory.SubFactory(TikTokShopFactory)
    sync_type = "orders"
    status = "success"
    message = ""
    orders_synced = 0
