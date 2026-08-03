from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.marketplace.shopee.product_match import ShopeeProductMatchService


class Command(BaseCommand):
    help = "Match Shopee items to ProductVariantMarketplace records by SKU for all active shops"

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            result = ShopeeProductMatchService().sync_all_active_shops_product_match()
        except Exception as e:
            raise CommandError(str(e)) from e
        self.stdout.write(self.style.SUCCESS(f"Done: {result}"))
