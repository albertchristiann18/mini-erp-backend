from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.purchasing.models import SourcingPoolItem


class Command(BaseCommand):
    help = "Mark pool items inactive if last_active_at is older than 6 months."

    def handle(self, *args: str, **options: str) -> None:
        cutoff = timezone.now() - timedelta(days=180)
        updated = SourcingPoolItem.objects.filter(
            is_active=True,
            is_used=False,
            last_active_at__lt=cutoff,
        ).update(is_active=False)
        self.stdout.write(f"Marked {updated} pool items inactive.")
