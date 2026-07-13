from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.marketplace.shopee.product_push import ShopeeProductPushService


class Command(BaseCommand):
    help = "Push ERP products without shopee_item_id to Shopee for all active shops"

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            result = ShopeeProductPushService().sync_all_active_shops_push()
        except Exception as e:
            raise CommandError(str(e)) from e
        self.stdout.write(self.style.SUCCESS(f"Done: {result}"))
