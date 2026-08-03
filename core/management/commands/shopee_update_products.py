from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.marketplace.shopee.product_push import ShopeeProductPushService


class Command(BaseCommand):
    help = "Re-sync all already-linked products from ERP to Shopee for all active shops"

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            result = ShopeeProductPushService().sync_all_active_shops_update_products()
        except Exception as e:
            raise CommandError(str(e)) from e
        self.stdout.write(self.style.SUCCESS(f"Done: {result}"))
