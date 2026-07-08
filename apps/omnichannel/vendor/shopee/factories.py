# mypy: disable-error-code="no-untyped-call"
import factory

from apps.catalog.factories import ProductVariantFactory
from apps.omnichannel.vendor.shopee.models import ShopeeShop, ShopeeStockSyncLog, ShopeeWebhookLog
from core.factories import CompanyFactory


class ShopeeShopFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ShopeeShop

    company = factory.SubFactory(CompanyFactory)
    shop_id = factory.Sequence(lambda n: 1000000 + n)
    shop_name = factory.Sequence(lambda n: f"Test Shop {n}")
    partner_id = 12345
    partner_key = "test_partner_key_abc123"
    access_token = "test_access_token"
    refresh_token = "test_refresh_token"
    is_sandbox = True
    is_active = True


class ShopeeWebhookLogFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ShopeeWebhookLog

    shop_id = factory.Sequence(lambda n: 1000000 + n)
    event_code = 3
    payload = factory.LazyAttribute(
        lambda o: {
            "ordersn": "TEST123",
            "status": "READY_TO_SHIP",
            "shop_id": o.shop_id,
        }
    )
    processed = False


class ShopeeStockSyncLogFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ShopeeStockSyncLog

    company = factory.SubFactory(CompanyFactory)
    shop = factory.SubFactory(ShopeeShopFactory)
    variant = factory.SubFactory(ProductVariantFactory)
    sku_variant_code = factory.Sequence(lambda n: f"SKU-VAR-{n:04d}")
    quantity_synced = 10
    success = True
    error_message = ""
    sync_type = ShopeeStockSyncLog.SyncType.SINGLE
    shopee_item_id = factory.Sequence(lambda n: 100000 + n)
    shopee_model_id = factory.Sequence(lambda n: 200000 + n)
    shopee_response = factory.LazyFunction(dict)
