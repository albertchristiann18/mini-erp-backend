import hashlib
import hmac
import logging
import time
from datetime import timedelta
from typing import Any, cast
from urllib.parse import urlencode

import requests
from django.utils import timezone

from apps.marketplace.shopee.exceptions import (
    ShopeeAPIError,
    ShopeeAuthError,
)
from apps.marketplace.shopee.models import ShopeeShop

logger = logging.getLogger(__name__)


class ShopeeClient:
    def __init__(self, shop: ShopeeShop) -> None:
        self.shop = shop

    def _sign(self, path: str) -> tuple[str, int]:
        timestamp = int(time.time())
        base_string = (
            f"{self.shop.partner_id}{path}{timestamp}{self.shop.access_token}{self.shop.shop_id}"
        )
        sign = hmac.new(
            self.shop.partner_key.encode(),
            base_string.encode(),
            hashlib.sha256,
        ).hexdigest()
        return sign, timestamp

    def _get_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _build_url(self, path: str, extra_params: dict[str, Any] | None = None) -> str:
        sign, timestamp = self._sign(path)
        params: dict[str, Any] = {
            "partner_id": self.shop.partner_id,
            "shop_id": self.shop.shop_id,
            "timestamp": timestamp,
            "access_token": self.shop.access_token,
            "sign": sign,
        }
        if extra_params:
            params.update(extra_params)
        return f"{self.shop.base_url}{path}?{urlencode(params)}"

    def _ensure_token_fresh(self) -> None:
        """Refresh access token if expired or expiring within 10 minutes."""
        if not self.shop.token_expires_at:
            return
        buffer = timedelta(minutes=10)
        if timezone.now() >= self.shop.token_expires_at - buffer:
            success = self.refresh_access_token()
            if not success:
                raise ShopeeAuthError(
                    status_code=401,
                    error_code="TOKEN_EXPIRED",
                    message="Access token expired and refresh failed",
                )

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._ensure_token_fresh()
        url = self._build_url(path, params)
        headers = self._get_headers()
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise ShopeeAPIError(
                status_code=resp.status_code,
                error_code="HTTP_ERROR",
                message=resp.text,
            )
        data = resp.json()
        if data.get("error"):
            raise ShopeeAPIError(
                status_code=resp.status_code,
                error_code=data["error"],
                message=data.get("message", ""),
            )
        return cast(dict[str, Any], data.get("response", data))

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        self._ensure_token_fresh()
        url = self._build_url(path)
        headers = self._get_headers()
        resp = requests.post(url, headers=headers, json=body or {}, timeout=30)
        if resp.status_code != 200:
            raise ShopeeAPIError(
                status_code=resp.status_code,
                error_code="HTTP_ERROR",
                message=resp.text,
            )
        data = resp.json()
        if data.get("error"):
            raise ShopeeAPIError(
                status_code=resp.status_code,
                error_code=data["error"],
                message=data.get("message", ""),
            )
        return cast(dict[str, Any], data.get("response", data))

    def refresh_access_token(self) -> bool:
        path = "/api/v2/auth/access_token/get"
        timestamp = int(time.time())
        base_string = f"{self.shop.partner_id}{path}{timestamp}"
        sign = hmac.new(
            self.shop.partner_key.encode(),
            base_string.encode(),
            hashlib.sha256,
        ).hexdigest()
        params = {
            "partner_id": self.shop.partner_id,
            "timestamp": timestamp,
            "sign": sign,
        }
        url = f"{self.shop.base_url}{path}?{urlencode(params)}"
        body = {
            "partner_id": self.shop.partner_id,
            "shop_id": self.shop.shop_id,
            "refresh_token": self.shop.refresh_token,
        }
        resp = requests.post(url, json=body, timeout=30)
        try:
            resp.raise_for_status()
        except requests.RequestException:
            return False
        data = resp.json()
        if data.get("error"):
            logger.warning(
                "Token refresh failed for shop %s: %s — %s",
                self.shop.shop_id,
                data.get("error"),
                data.get("message", ""),
            )
            return False
        self.shop.access_token = data["access_token"]
        self.shop.refresh_token = data["refresh_token"]
        self.shop.token_expires_at = timezone.now() + timedelta(
            seconds=data.get("expire_in", 14400),
        )
        self.shop.save(update_fields=["access_token", "refresh_token", "token_expires_at"])
        return True

    def update_stock(self, item_id: int, stock_list: list[dict[str, Any]]) -> dict[str, Any]:
        return self.post(
            "/api/v2/product/update_stock",
            {"item_id": item_id, "stock_list": stock_list},
        )

    def get_item_base_info(self, item_id_list: list[int]) -> dict[str, Any]:
        return self.get(
            "/api/v2/product/get_item_base_info",
            {"item_id_list": ",".join(str(i) for i in item_id_list)},
        )

    def get_model_list(self, item_id: int) -> dict[str, Any]:
        return self.get(
            "/api/v2/product/get_model_list",
            {"item_id": item_id},
        )

    # ── Backward-compatible wrappers ─────────────────────────────────────────

    def get_order_detail(self, order_sn_list: list[str]) -> dict[str, Any]:
        return self.get(
            "/api/v2/order/get_order_detail",
            {
                "order_sn_list": ",".join(order_sn_list),
                "response_optional_fields": (
                    "buyer_user_id,buyer_username,estimated_shipping_fee,"
                    "recipient_address,actual_shipping_fee,goods_to_declare,"
                    "note,note_update_time,item_list,pay_time,dropshipper,"
                    "dropshipper_phone,split_up,buyer_cancel_reason,cancel_by,"
                    "cancel_reason,actual_shipping_fee_confirmed,buyer_cpf_id,"
                    "fulfillment_flag,pickup_done_time,package_list,"
                    "shipping_carrier,payment_method,total_amount,buyer_username,"
                    "invoice_data,checkout_shipping_carrier,reverse_shipping_fee,"
                    "order_chargeable_weight_gram"
                ),
            },
        )

    def get_order_list(
        self,
        time_range_field: str = "create_time",
        time_from: int = 0,
        time_to: int = 0,
        page_size: int = 50,
        cursor: str = "",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "time_range_field": time_range_field,
            "time_from": time_from,
            "time_to": time_to,
            "page_size": page_size,
            "order_status": "READY_TO_SHIP",
        }
        if cursor:
            params["cursor"] = cursor
        return self.get("/api/v2/order/get_order_list", params)

    def ship_order(
        self,
        order_sn: str,
        tracking_number: str = "",
        pickup_time_id: str = "",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"order_sn": order_sn}
        if tracking_number:
            body["package_number"] = ""
            body["dropoff"] = {"tracking_no": tracking_number}
        return self.post("/api/v2/logistics/ship_order", body)

    def get_item_list(
        self,
        offset: int = 0,
        page_size: int = 50,
        item_status: str = "NORMAL",
    ) -> dict[str, Any]:
        return self.get(
            "/api/v2/product/get_item_list",
            {
                "offset": offset,
                "page_size": page_size,
                "item_status": item_status,
            },
        )

    def get_shop_info(self) -> dict[str, Any]:
        return self.get("/api/v2/shop/get_shop_info")

    def get_escrow_detail(self, order_sn: str) -> dict[str, Any]:
        return self.get(
            "/api/v2/payment/get_escrow_detail",
            {"order_sn": order_sn},
        )

    # ── Product push methods ───────────────────────────────────────────

    def get_channel_list(self) -> dict[str, Any]:
        return self.get("/api/v2/logistics/get_channel_list")

    def upload_image(self, image_file: Any) -> str:
        """Upload image to Shopee media space. Returns image_id string."""
        self._ensure_token_fresh()
        path = "/api/v2/media_space/upload_image"
        sign, timestamp = self._sign(path)
        params = {
            "partner_id": self.shop.partner_id,
            "shop_id": self.shop.shop_id,
            "timestamp": timestamp,
            "access_token": self.shop.access_token,
            "sign": sign,
        }
        url = f"{self.shop.base_url}{path}"
        resp = requests.post(url, params=params, files={"file": image_file}, timeout=60)
        if resp.status_code != 200:
            raise ShopeeAPIError(
                status_code=resp.status_code, error_code="HTTP_ERROR", message=resp.text
            )
        data = resp.json()
        if data.get("error"):
            raise ShopeeAPIError(
                status_code=resp.status_code,
                error_code=data["error"],
                message=data.get("message", ""),
            )
        return str(cast(dict[str, Any], data.get("response", data)).get("image_id", ""))

    def add_item(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post("/api/v2/product/add_item", payload)

    def add_model(self, item_id: int, models: list[dict[str, Any]]) -> dict[str, Any]:
        return self.post("/api/v2/product/add_model", {"item_id": item_id, "model": models})

    def update_item(self, item_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post("/api/v2/product/update_item", {"item_id": item_id, **payload})

    def update_price(self, item_id: int, price_list: list[dict[str, Any]]) -> dict[str, Any]:
        return self.post(
            "/api/v2/product/update_price",
            {"item_id": item_id, "price_list": price_list},
        )

    def get_shipping_document_result(
        self,
        order_sn_list: list[str],
        document_type: str = "THERMAL_AIR_WAYBILL",
    ) -> dict[str, Any]:
        return self.post(
            "/api/v2/logistics/get_shipping_document_result",
            {
                "order_list": [{"order_sn": sn, "package_number": ""} for sn in order_sn_list],
                "shipping_document_type": document_type,
            },
        )
