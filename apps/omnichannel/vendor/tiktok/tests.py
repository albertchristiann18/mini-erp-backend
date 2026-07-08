import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.catalog.factories import ProductFactory, ProductVariantFactory
from apps.inventory.factories import ProductCogsFactory, ProductVariantWarehouseFactory
from apps.omnichannel.vendor.tiktok.factories import (
    TikTokShopFactory,
    TikTokWebhookLogFactory,
)
from apps.omnichannel.vendor.tiktok.models import TikTokSyncLog, TikTokWebhookLog
from apps.omnichannel.vendor.tiktok.views import _verify_signature
from apps.sales.models import SalesOrder
from core.factories import CompanyFactory, WarehouseFactory


class TikTokWebhookAPITest(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.company = CompanyFactory()
        self.shop = TikTokShopFactory(company=self.company)

    def test_webhook_receives_order_event(self):
        payload = {
            "type": "order.status_update",
            "shop_id": self.shop.shop_id,
            "order_id": "TT_ORDER_001",
            "status": "AWAITING_SHIPMENT",
        }
        response = self.client.post(
            "/tiktok/webhook/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TikTokWebhookLog.objects.count(), 1)
        log = TikTokWebhookLog.objects.first()
        self.assertEqual(log.shop, self.shop)
        self.assertEqual(log.event_type, "order.status_update")

    def test_webhook_invalid_json(self):
        response = self.client.post(
            "/tiktok/webhook/",
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class TikTokOrderSyncTest(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.product = ProductFactory(company=self.company)
        self.variant = ProductVariantFactory(
            product=self.product,
            company=self.company,
            total_available_qty=100,
        )
        self.variant.refresh_from_db()
        self.pvw = ProductVariantWarehouseFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=100,
        )
        self.cogs = ProductCogsFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            original_qty=100,
            remaining_qty=100,
            cogs_amount=50000,
        )
        self.shop = TikTokShopFactory(
            company=self.company,
            warehouse=self.warehouse,
        )

    @patch("apps.omnichannel.vendor.tiktok.order_sync.TikTokClient")
    def test_order_upsert_creates_sales_order(self, MockClient):
        sku = self.variant.sku_variant_code
        mock_client = MockClient.return_value
        mock_client.get.return_value = {
            "order": {
                "order_id": "TT_ORDER_001",
                "status": "AWAITING_SHIPMENT",
                "shipping_provider": "JNE",
                "tracking_number": "TRK001",
                "shipping_fee": 10000,
                "recipient_address": {
                    "name": "John Doe",
                    "phone": "08123456789",
                    "full_address": "Jl. Test 123",
                    "city": "Jakarta",
                    "state": "DKI Jakarta",
                },
                "line_items": [
                    {
                        "seller_sku": sku,
                        "original_price": 100000,
                        "sale_price": 100000,
                        "quantity": 2,
                    }
                ],
            }
        }

        from apps.omnichannel.vendor.tiktok.order_sync import TikTokOrderSyncer

        syncer = TikTokOrderSyncer(self.shop)
        so = syncer.upsert_order(mock_client.get.return_value["order"])

        self.assertIsNotNone(so)
        self.assertEqual(so.marketplace_order_id, "TT_ORDER_001")
        self.assertEqual(so.customer_name, "John Doe")
        self.assertEqual(so.items.count(), 1)
        item = so.items.first()
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.selling_price, 100000)

    @patch("apps.omnichannel.vendor.tiktok.order_sync.TikTokClient")
    def test_order_upsert_skips_duplicate(self, MockClient):
        sku = self.variant.sku_variant_code
        order_data = {
            "order_id": "TT_ORDER_DUP",
            "status": "AWAITING_SHIPMENT",
            "shipping_provider": "",
            "tracking_number": "",
            "shipping_fee": 0,
            "recipient_address": {
                "name": "Jane",
                "phone": "081",
                "full_address": "Addr",
                "city": "City",
                "state": "State",
            },
            "line_items": [
                {
                    "seller_sku": sku,
                    "original_price": 100000,
                    "sale_price": 100000,
                    "quantity": 1,
                }
            ],
        }

        from apps.omnichannel.vendor.tiktok.order_sync import TikTokOrderSyncer

        syncer = TikTokOrderSyncer(self.shop)
        so1 = syncer.upsert_order(order_data)
        so2 = syncer.upsert_order(order_data)

        self.assertIsNotNone(so1)
        self.assertIsNotNone(so2)
        self.assertEqual(SalesOrder.objects.filter(marketplace_order_id="TT_ORDER_DUP").count(), 1)


class TikTokShopAPITest(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.company = CompanyFactory()
        self.shop = TikTokShopFactory(company=self.company)

    def test_tiktok_shop_api_list(self):
        response = self.client.get("/tiktok/shops/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_webhook_log_api_list(self):
        TikTokWebhookLogFactory(shop=self.shop)
        response = self.client.get("/tiktok/webhook-logs/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class TikTokUtilsTest(TestCase):
    def test_hmac_signature(self):
        secret = "my_secret_key"
        body = b'{"order_id": "123"}'
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        self.assertTrue(_verify_signature(secret, body, expected))
        self.assertFalse(_verify_signature(secret, body, "wrong_signature"))
        self.assertEqual(len(expected), 64)


class TikTokRefreshTokenTest(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.company = CompanyFactory()
        self.shop = TikTokShopFactory(company=self.company)

    @patch("requests.post")
    def test_refresh_token_action(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": "0",
            "data": {
                "access_token": "new_access_token",
                "refresh_token": "new_refresh_token",
                "expires_in": 14400,
            },
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        response = self.client.post(f"/tiktok/shops/{self.shop.id}/refresh-token/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.access_token, "new_access_token")
        self.assertEqual(self.shop.refresh_token, "new_refresh_token")


class TestTikTokSyncScripts(TestCase):
    def test_scripts_tiktok_sync_orders_run_creates_sync_log(self):
        company = CompanyFactory()
        warehouse = WarehouseFactory(company=company)
        shop = TikTokShopFactory(company=company, warehouse=warehouse, is_active=True)

        with patch(
            "apps.omnichannel.vendor.tiktok.order_sync.TikTokOrderSyncer.sync_orders",
            return_value=2,
        ):
            from scripts.tiktok_sync_orders import run

            run()

        log = TikTokSyncLog.objects.get(shop=shop, sync_type="orders")
        self.assertEqual(log.status, "success")
        self.assertEqual(log.orders_synced, 2)

    def test_scripts_tiktok_sync_orders_run_filters_by_shop_id(self):
        company = CompanyFactory()
        warehouse = WarehouseFactory(company=company)
        shop1 = TikTokShopFactory(company=company, warehouse=warehouse, is_active=True)
        TikTokShopFactory(company=company, warehouse=warehouse, is_active=True)

        with patch(
            "apps.omnichannel.vendor.tiktok.order_sync.TikTokOrderSyncer.sync_orders",
            return_value=1,
        ):
            from scripts.tiktok_sync_orders import run

            run(shop_id=shop1.shop_id)

        logs = TikTokSyncLog.objects.filter(sync_type="orders")
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.first().shop, shop1)

    def test_scripts_tiktok_sync_orders_run_logs_error_on_exception(self):
        company = CompanyFactory()
        warehouse = WarehouseFactory(company=company)
        shop = TikTokShopFactory(company=company, warehouse=warehouse, is_active=True)

        with patch(
            "apps.omnichannel.vendor.tiktok.order_sync.TikTokOrderSyncer.sync_orders",
            side_effect=Exception("fail"),
        ):
            from scripts.tiktok_sync_orders import run

            run()

        log = TikTokSyncLog.objects.get(shop=shop, sync_type="orders")
        self.assertEqual(log.status, "error")
        self.assertEqual(log.message, "fail")

    def test_scripts_tiktok_sync_stock_run_creates_sync_log(self):
        company = CompanyFactory()
        warehouse = WarehouseFactory(company=company)
        shop = TikTokShopFactory(company=company, warehouse=warehouse, is_active=True)

        with patch(
            "apps.omnichannel.vendor.tiktok.stock_sync.TikTokStockSyncer.push_stock",
            return_value=4,
        ):
            from scripts.tiktok_sync_stock import run

            run()

        log = TikTokSyncLog.objects.get(shop=shop, sync_type="stock")
        self.assertEqual(log.status, "success")
        self.assertEqual(log.orders_synced, 4)

    def test_scripts_tiktok_sync_stock_run_logs_error_on_exception(self):
        company = CompanyFactory()
        warehouse = WarehouseFactory(company=company)
        shop = TikTokShopFactory(company=company, warehouse=warehouse, is_active=True)

        with patch(
            "apps.omnichannel.vendor.tiktok.stock_sync.TikTokStockSyncer.push_stock",
            side_effect=Exception("stock fail"),
        ):
            from scripts.tiktok_sync_stock import run

            run()

        log = TikTokSyncLog.objects.get(shop=shop, sync_type="stock")
        self.assertEqual(log.status, "error")
