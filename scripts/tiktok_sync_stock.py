"""
Push stock levels to TikTok for all active shops

Usage:
    uv run python scripts/tiktok_sync_stock.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
import django

django.setup()

from django.utils import timezone

from apps.marketplace.tiktok.models import TikTokShop, TikTokSyncLog
from apps.marketplace.tiktok.stock_sync import TikTokStockSyncer


def run() -> None:
    for shop in TikTokShop.objects.filter(is_active=True):
        log = TikTokSyncLog.objects.create(shop=shop, sync_type="stock", status="running")
        try:
            syncer = TikTokStockSyncer(shop)
            count = syncer.push_stock()
            log.status = "success"
            log.orders_synced = count
            print(f"Shop {shop.shop_id}: pushed {count} stock records")
        except Exception as e:
            log.status = "error"
            log.message = str(e)
        finally:
            log.finished_at = timezone.now()
            log.save()


if __name__ == "__main__":
    run()
