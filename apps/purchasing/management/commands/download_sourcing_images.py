from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand

from apps.purchasing.models import SourcingPool
from apps.purchasing.services.sourcing_service import SourcingService


class Command(BaseCommand):
    help = "Download pending sourcing pool images from supplier URLs to R2"

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--pool-id", type=str, help="Download images for a specific pool only")
        parser.add_argument(
            "--include-failed",
            action="store_true",
            help="Also retry items with FAILED status",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        service = SourcingService()
        pool_id: str | None = options.get("pool_id")
        include_failed: bool = options["include_failed"]

        if pool_id:
            try:
                pools = [SourcingPool.objects.select_related("supplier").get(id=pool_id)]
            except SourcingPool.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"Pool {pool_id} not found"))
                return
        else:
            pools = list(SourcingPool.objects.select_related("supplier").all())

        total_done = 0
        total_failed = 0
        total_skipped = 0

        for pool in pools:
            result = service.download_pool_images(pool=pool, include_failed=include_failed)
            total_done += result["done"]
            total_failed += result["failed"]
            total_skipped += result["skipped"]
            self.stdout.write(
                f"Pool {pool.id} ({pool.supplier.name}): "
                f"done={result['done']} failed={result['failed']} skipped={result['skipped']}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Complete — done={total_done} failed={total_failed} skipped={total_skipped}"
            )
        )
