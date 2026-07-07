"""
Sync orders from TikTok for all active shops

Usage:
    uv run python scripts/tiktok_sync_orders.py [--shop-id SHOP_ID]
"""

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
import django

django.setup()

from django.utils import timezone

from apps.omnichannel.vendor.tiktok.models import TikTokShop, TikTokSyncLog
from apps.omnichannel.vendor.tiktok.order_sync import TikTokOrderSyncer

logger = logging.getLogger(__name__)


def run(shop_id: str | None = None) -> None:
    shops = TikTokShop.objects.filter(is_active=True)
    if shop_id is not None:
        shops = shops.filter(shop_id=shop_id)

    for shop in shops:
        log = TikTokSyncLog.objects.create(shop=shop, sync_type="orders", status="running")
        try:
            syncer = TikTokOrderSyncer(shop)
            count = syncer.sync_orders()
            log.status = "success"
            log.orders_synced = count
            print(f"Shop {shop.shop_id}: synced {count} orders")
        except Exception as e:
            log.status = "error"
            log.message = str(e)
            print(f"Shop {shop.shop_id}: {e}")
        finally:
            log.finished_at = timezone.now()
            log.save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync orders from TikTok for all active shops")
    parser.add_argument("--shop-id", type=str, help="Sync only this shop_id")
    args = parser.parse_args()
    run(shop_id=args.shop_id)
