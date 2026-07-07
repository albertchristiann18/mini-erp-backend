"""
Sync stock from Shopee for all active shops

Usage:
    uv run python scripts/shopee_sync_stock.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
import django

django.setup()

from django.utils import timezone

from apps.omnichannel.vendor.shopee.models import ShopeeShop, ShopeeSyncLog
from apps.omnichannel.vendor.shopee.stock_sync import ShopeeStockSyncService


def run() -> None:
    for shop in ShopeeShop.objects.filter(is_active=True):
        log = ShopeeSyncLog.objects.create(shop=shop, sync_type="stock", status="running")
        try:
            service = ShopeeStockSyncService()
            result = service.sync_all_variants(shop)
            count = result["success"]
            log.status = "success"
            log.records_synced = count
            print(f"Shop {shop.shop_id}: synced {count} stock records")
        except Exception as e:
            log.status = "failed"
            log.error_message = str(e)
        finally:
            log.finished_at = timezone.now()
            log.save()


if __name__ == "__main__":
    run()
