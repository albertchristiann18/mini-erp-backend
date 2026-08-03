import hashlib
import hmac
import time


def get_timestamp() -> int:
    return int(time.time())


def sign_shop_api(
    partner_id: int,
    path: str,
    timestamp: int,
    access_token: str,
    shop_id: int,
    partner_key: str,
) -> str:
    """Signature for Shop-level API calls."""
    base = f"{partner_id}{path}{timestamp}{access_token}{shop_id}"
    return hmac.new(partner_key.encode(), base.encode(), hashlib.sha256).hexdigest()


def sign_public_api(partner_id: int, path: str, timestamp: int, partner_key: str) -> str:
    """Signature for Public API calls (no access_token, no shop_id)."""
    base = f"{partner_id}{path}{timestamp}"
    return hmac.new(partner_key.encode(), base.encode(), hashlib.sha256).hexdigest()


def verify_webhook_signature(partner_key: str, raw_body: bytes, shopee_signature: str) -> bool:
    """Verify incoming webhook signature from Shopee."""
    computed = hmac.new(partner_key.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, shopee_signature)
