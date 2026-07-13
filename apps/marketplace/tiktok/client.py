import logging
from datetime import timedelta
from typing import Any, Dict, Optional

import requests
from django.utils import timezone

from apps.marketplace.tiktok.models import TikTokShop

logger = logging.getLogger(__name__)

BASE_URL = "https://open.tiktokapis.com"


class TikTokAPIError(Exception):
    def __init__(self, error_code: str, message: str):
        self.error_code = error_code
        self.message = message
        super().__init__(f"[{error_code}] {message}")


class TikTokClient:
    """Handles all TikTok Shop API calls for a single shop."""

    def __init__(self, shop: TikTokShop):
        self.shop = shop
        self._retried = False

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.shop.access_token}",
            "Content-Type": "application/json",
        }

    def get(self, path: str, params: Optional[Dict] = None) -> Any:
        url = f"{BASE_URL}{path}"
        resp = requests.get(url, params=params, headers=self._get_headers(), timeout=30)
        return self._handle_response(resp, "GET", path, params=params)  # type: ignore[call-arg]

    def post(self, path: str, data: Optional[Dict] = None) -> Any:
        url = f"{BASE_URL}{path}"
        resp = requests.post(url, json=data, headers=self._get_headers(), timeout=30)
        return self._handle_response(resp, "POST", path, data=data)  # type: ignore[call-arg]

    def _handle_response(
        self, response: requests.Response, method: str, path: str, **kwargs: Any
    ) -> Any:
        result = response.json()

        error_code = result.get("code") or result.get("error")
        if error_code == "access_token_invalid" and not self._retried:
            self._retried = True
            self.refresh_access_token()
            if method == "GET":
                return self.get(path, kwargs.get("params"))
            else:
                return self.post(path, kwargs.get("data"))

        if not response.ok:
            raise TikTokAPIError(
                str(error_code or response.status_code),
                result.get("message", response.text),
            )

        if error_code and str(error_code) != "0":
            raise TikTokAPIError(str(error_code), result.get("message", ""))

        self._retried = False
        return result.get("data", result)

    def refresh_access_token(self) -> None:
        """Use refresh_token to get a new access_token."""
        url = f"{BASE_URL}/v2/oauth/token/"
        body = {
            "app_key": self.shop.app_key,
            "app_secret": self.shop.app_secret,
            "grant_type": "refresh_token",
            "refresh_token": self.shop.refresh_token,
        }
        resp = requests.post(url, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        error_code = data.get("code") or data.get("error")
        if error_code and str(error_code) != "0":
            raise TikTokAPIError(str(error_code), data.get("message", ""))

        token_data = data.get("data", data)
        self.shop.access_token = token_data["access_token"]
        self.shop.refresh_token = token_data["refresh_token"]
        self.shop.token_expires_at = timezone.now() + timedelta(
            seconds=token_data.get("expires_in", 14400)
        )
        self.shop.save(update_fields=["access_token", "refresh_token", "token_expires_at"])
        logger.info(f"Refreshed token for TikTok shop {self.shop.shop_id}")
