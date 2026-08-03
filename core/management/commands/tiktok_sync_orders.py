from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.marketplace.tiktok.order_sync import sync_all_active_shops_orders


class Command(BaseCommand):
    help = "Sync orders from TikTok for all active shops"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--shop-id", type=str, default=None)
        parser.add_argument("--hours", type=int, default=24)

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            result = sync_all_active_shops_orders(
                shop_id=options["shop_id"],
                hours=options["hours"],
            )
        except Exception as e:
            raise CommandError(str(e)) from e
        self.stdout.write(self.style.SUCCESS(f"Done: {result}"))
