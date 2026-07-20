import json
from unittest.mock import MagicMock, patch

from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.marketplace.tiktok.models import TikTokWebhookLog
from apps.marketplace.tiktok.tests.factories import TikTokShopFactory, TikTokWebhookLogFactory
from core.factories import CompanyFactory


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
