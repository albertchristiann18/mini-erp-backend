import hashlib
import hmac

from django.test import TestCase

from apps.marketplace.tiktok.views import _verify_signature


class TikTokUtilsTest(TestCase):
    def test_hmac_signature(self):
        secret = "my_secret_key"
        body = b'{"order_id": "123"}'
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        self.assertTrue(_verify_signature(secret, body, expected))
        self.assertFalse(_verify_signature(secret, body, "wrong_signature"))
        self.assertEqual(len(expected), 64)
