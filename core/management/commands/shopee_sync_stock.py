from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.marketplace.shopee.stock_sync import ShopeeStockSyncService


class Command(BaseCommand):
    help = "Sync stock to Shopee for all active shops"

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            result = ShopeeStockSyncService().sync_all_active_shops_stock()
        except Exception as e:
            raise CommandError(str(e)) from e
        self.stdout.write(self.style.SUCCESS(f"Done: {result}"))
