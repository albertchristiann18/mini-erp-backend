"""
Match Shopee items to ProductVariantMarketplace records by SKU

Usage:
    uv run python scripts/shopee_match_products.py
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
from apps.omnichannel.vendor.shopee.product_match import ShopeeProductMatchService

logger = logging.getLogger(__name__)


def run() -> None:
    shops = ShopeeShop.objects.filter(is_active=True)
    for shop in shops:
        log = ShopeeSyncLog.objects.create(
            shop=shop,
            sync_type="product_match",
            status="running",
        )
        try:
            result = ShopeeProductMatchService().match_products_for_shop(shop)
            log.status = "success"
            log.records_synced = result["matched"]
            log.finished_at = timezone.now()
            log.save(update_fields=["status", "records_synced", "finished_at"])
            print(
                f"Shop {shop.shop_id}: matched={result['matched']} skipped={result['skipped']} errors={len(result['errors'])}"
            )
        except Exception as e:
            log.status = "failed"
            log.error_message = str(e)
            log.finished_at = timezone.now()
            log.save(update_fields=["status", "error_message", "finished_at"])
            logger.exception("shopee_match_products failed for shop %s", shop.shop_id)


if __name__ == "__main__":
    run()
