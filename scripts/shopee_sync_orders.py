"""
Sync orders from Shopee for all active shops

Usage:
    uv run python scripts/shopee_sync_orders.py [--shop-id SHOP_ID] [--hours HOURS]
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

from apps.omnichannel.vendor.shopee.models import ShopeeShop, ShopeeSyncLog
from apps.omnichannel.vendor.shopee.order_sync import ShopeeOrderSyncer

logger = logging.getLogger(__name__)


def run(shop_id: int | None = None, hours: int = 24) -> None:
    shops = ShopeeShop.objects.filter(is_active=True)
    if shop_id is not None:
        shops = shops.filter(shop_id=shop_id)

    for shop in shops:
        log = ShopeeSyncLog.objects.create(shop=shop, sync_type="orders", status="running")
        try:
            syncer = ShopeeOrderSyncer(shop)
            count = syncer.sync_recent_orders(hours_back=hours)
            log.status = "success"
            log.records_synced = count
            print(f"Shop {shop.shop_id}: synced {count} orders")
        except Exception as e:
            log.status = "failed"
            log.error_message = str(e)
            print(f"Shop {shop.shop_id}: {e}")
        finally:
            log.finished_at = timezone.now()
            log.save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync orders from Shopee for all active shops")
    parser.add_argument("--shop-id", type=int, help="Sync only this shop_id")
    parser.add_argument("--hours", type=int, default=24, help="Hours back to sync")
    args = parser.parse_args()
    run(shop_id=args.shop_id, hours=args.hours)
