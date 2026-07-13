import logging

from django.utils import timezone

from apps.inventory.models import ProductVariantWarehouse
from apps.marketplace.tiktok.client import TikTokAPIError, TikTokClient
from apps.marketplace.tiktok.models import TikTokShop, TikTokSyncLog

logger = logging.getLogger(__name__)


class TikTokStockSyncer:
    def __init__(self, shop: TikTokShop):
        self.shop = shop
        self.client = TikTokClient(shop)

    def push_stock(self, variant_ids: list | None = None) -> int:
        """Push current stock levels to TikTok."""
        if not self.shop.warehouse:
            logger.warning(f"Shop {self.shop.shop_id} has no warehouse configured")
            return 0

        qs = ProductVariantWarehouse.objects.filter(
            warehouse=self.shop.warehouse,
        ).select_related("product_variant")

        if variant_ids:
            qs = qs.filter(product_variant_id__in=variant_ids)

        count = 0
        for pvw in qs:
            sku = pvw.product_variant.sku_variant_code
            if not sku:
                continue
            try:
                self.client.post(
                    "/api/products/stocks/update",
                    data={
                        "sku": sku,
                        "available_stock": pvw.physical_qty,
                    },
                )
                count += 1
            except TikTokAPIError as e:
                logger.error(f"Failed to push stock for SKU {sku}: {e}")

        return count


def sync_all_active_shops_stock() -> dict[str, int]:
    """Push stock to TikTok for all active shops. Returns summary."""
    total_count = 0
    shops_processed = 0

    for shop in TikTokShop.objects.filter(is_active=True):
        log = TikTokSyncLog.objects.create(shop=shop, sync_type="stock", status="running")
        try:
            syncer = TikTokStockSyncer(shop)
            count = syncer.push_stock()
            log.status = "success"
            log.orders_synced = count
            shops_processed += 1
            total_count += count
        except Exception as e:
            log.status = "error"
            log.message = str(e)
        finally:
            log.finished_at = timezone.now()
            log.save()

    return {"shops": shops_processed, "synced": total_count}
