from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.marketplace.tiktok.stock_sync import sync_all_active_shops_stock


class Command(BaseCommand):
    help = "Push stock to TikTok for all active shops"

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            result = sync_all_active_shops_stock()
        except Exception as e:
            raise CommandError(str(e)) from e
        self.stdout.write(self.style.SUCCESS(f"Done: {result}"))
