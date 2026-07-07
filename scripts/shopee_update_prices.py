"""
Push current selling prices from ERP to Shopee for all active shops

Usage:
    uv run python scripts/shopee_update_prices.py
"""

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
import django

django.setup()

from django.utils import timezone

from apps.omnichannel.vendor.shopee.models import ShopeeShop, ShopeeSyncLog
from apps.omnichannel.vendor.shopee.product_push import ShopeeProductPushService

logger = logging.getLogger(__name__)


def run() -> None:
    from apps.inventory.models import ProductVariantMarketplace

    shops = ShopeeShop.objects.filter(is_active=True).select_related("marketplace")
    service = ShopeeProductPushService()

    for shop in shops:
        if not shop.marketplace:
            continue

        listings = ProductVariantMarketplace.objects.filter(
            marketplace=shop.marketplace,
            is_active=True,
            shopee_item_id__isnull=False,
            shopee_model_id__isnull=False,
        )

        log = ShopeeSyncLog.objects.create(shop=shop, sync_type="product_price", status="running")
        updated_count = 0
        all_errors: list[str] = []

        try:
            for listing in listings:
                result = service.update_price_for_listing(listing, shop)
                if result["updated"]:
                    updated_count += 1
                if result["errors"]:
                    all_errors.extend([f"{listing.id}: {e}" for e in result["errors"]])

            log.status = "failed" if updated_count == 0 and all_errors else "success"
            log.records_synced = updated_count
            log.error_message = "\n".join(all_errors[:50])
        except Exception as e:
            log.status = "failed"
            log.error_message = str(e)
        finally:
            log.finished_at = timezone.now()
            log.save(update_fields=["status", "records_synced", "error_message", "finished_at"])

        print(f"Shop {shop.shop_id}: updated={updated_count} errors={len(all_errors)}")


if __name__ == "__main__":
    run()
