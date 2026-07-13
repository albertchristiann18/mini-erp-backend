import logging

from apps.inventory.models import ProductVariantWarehouse
from apps.marketplace.tiktok.client import TikTokAPIError, TikTokClient
from apps.marketplace.tiktok.models import TikTokShop

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
