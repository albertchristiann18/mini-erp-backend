import json
from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.marketplace.shopee.models import ShopeeWebhookLog
from apps.marketplace.shopee.tests.factories import ShopeeShopFactory, ShopeeWebhookLogFactory
from core.factories import CompanyFactory


class ShopeeWebhookAPITest(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.company = CompanyFactory()
        self.shop = ShopeeShopFactory(company=self.company)

    def test_webhook_receives_order_event(self):
        payload = {
            "code": 3,
            "shop_id": self.shop.shop_id,
            "ordersn": "SH_ORDER_001",
            "status": "READY_TO_SHIP",
        }
        response = self.client.post(
            "/shopee/webhook/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ShopeeWebhookLog.objects.count(), 1)
        log = ShopeeWebhookLog.objects.first()
        self.assertEqual(log.shop_id, self.shop.shop_id)
        self.assertEqual(log.event_code, 3)

    def test_webhook_invalid_json(self):
        response = self.client.post(
            "/shopee/webhook/",
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class ShopeeShopAPITest(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.company = CompanyFactory()
        self.shop = ShopeeShopFactory(company=self.company)

    def test_shopee_shop_api_list(self):
        response = self.client.get("/shopee/shops/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_webhook_log_api_list(self):
        ShopeeWebhookLogFactory(shop_id=self.shop.shop_id)
        response = self.client.get("/shopee/webhook-logs/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @patch(
        "apps.marketplace.shopee.order_sync.ShopeeOrderSyncer.sync_recent_orders",
        return_value=7,
    )
    def test_sync_orders_view_action_calls_sync_recent_orders(self, mock_sync):
        response = self.client.post(f"/shopee/shops/{self.shop.id}/sync-orders/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"synced": 7})
        mock_sync.assert_called_once()

    @patch(
        "apps.marketplace.shopee.order_sync.ShopeeOrderSyncer.sync_recent_orders",
        side_effect=Exception("boom"),
    )
    def test_sync_orders_view_action_returns_500_on_exception(self, mock_sync):
        response = self.client.post(f"/shopee/shops/{self.shop.id}/sync-orders/")
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(response.data, {"error": "boom"})


class ShopeeRefreshTokenTest(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.company = CompanyFactory()
        self.shop = ShopeeShopFactory(company=self.company)

    @patch("requests.post")
    def test_refresh_token_action(self, mock_post):
        from unittest.mock import MagicMock

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
            "expire_in": 14400,
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        response = self.client.post(f"/shopee/shops/{self.shop.id}/refresh-token/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.access_token, "new_access_token")
        self.assertEqual(self.shop.refresh_token, "new_refresh_token")
