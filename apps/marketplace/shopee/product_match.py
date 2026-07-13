import logging
from typing import Any

from apps.marketplace.shopee.client import ShopeeClient
from apps.marketplace.shopee.exceptions import ShopeeAPIError
from apps.marketplace.shopee.models import ShopeeShop

logger = logging.getLogger(__name__)


class ShopeeProductMatchService:
    """
    Pulls all items from Shopee and writes shopee_item_id / shopee_model_id
    back into ProductVariantMarketplace by matching:
      - item_sku (SKU INDUK)  ->  single-variant items: match to ProductVariant.sku_variant_code
      - model_sku             ->  multi-variant items:  match to ProductVariant.sku_variant_code
    Only updates existing ProductVariantMarketplace records. Never creates new ones.
    Never raises — errors are returned in the result dict.
    """

    def match_products_for_shop(self, shop: ShopeeShop) -> dict[str, Any]:
        from apps.catalog.models import ProductVariant, ProductVariantMarketplace
        from core.models import MarketplaceConnection

        matched = 0
        skipped = 0
        errors: list[str] = []

        # Resolve marketplace and company
        connection = (
            MarketplaceConnection.objects.select_related("company")
            .filter(shopee_shop=shop, is_active=True)
            .first()
        )
        if not connection:
            return {
                "matched": 0,
                "skipped": 0,
                "errors": ["No active MarketplaceConnection for this shop"],
            }

        marketplace = shop.marketplace
        if not marketplace:
            return {"matched": 0, "skipped": 0, "errors": ["ShopeeShop has no linked Marketplace"]}

        client = ShopeeClient(shop)

        # Step 1: paginate get_item_list to collect all item_ids
        item_ids: list[int] = []
        try:
            offset = 0
            page_size = 50
            while True:
                resp = client.get_item_list(offset=offset, page_size=page_size)
                batch = resp.get("item_id_list", [])
                item_ids.extend(batch)
                if not resp.get("has_next_page", False):
                    break
                offset += page_size
        except ShopeeAPIError as e:
            return {"matched": 0, "skipped": 0, "errors": [f"get_item_list failed: {e}"]}

        if not item_ids:
            return {"matched": 0, "skipped": 0, "errors": []}

        # Step 2: get_item_base_info in batches of 50
        items: list[dict[str, Any]] = []
        for i in range(0, len(item_ids), 50):
            batch = item_ids[i : i + 50]
            try:
                resp = client.get_item_base_info(batch)
                items.extend(resp.get("item_list", []))
            except ShopeeAPIError as e:
                errors.append(f"get_item_base_info batch {i}: {e}")
                continue

        # Step 3: for each item, match to ProductVariantMarketplace
        for item in items:
            item_id: int = item["item_id"]
            item_sku: str = item.get("item_sku", "").strip()

            try:
                # Get models for this item
                model_resp = client.get_model_list(item_id)
                models = [m for m in model_resp.get("model", []) if m.get("model_sku", "").strip()]
            except ShopeeAPIError as e:
                errors.append(f"get_model_list item {item_id}: {e}")
                skipped += 1
                continue

            if models:
                # Multi-variant item: match model_sku -> sku_variant_code
                for model in models:
                    model_sku: str = model.get("model_sku", "").strip()
                    model_id: int = model.get("model_id", 0)
                    if not model_sku:
                        skipped += 1
                        continue
                    variant = ProductVariant.objects.filter(sku_variant_code=model_sku).first()
                    if not variant:
                        skipped += 1
                        continue
                    updated = ProductVariantMarketplace.objects.filter(
                        product_variant=variant,
                        marketplace=marketplace,
                        product_variant__company=connection.company,
                    ).update(shopee_item_id=item_id, shopee_model_id=model_id)
                    if updated:
                        matched += updated
                    else:
                        skipped += 1
            else:
                # Single-variant item (no models): match item_sku -> sku_variant_code
                if not item_sku:
                    skipped += 1
                    continue
                variant = ProductVariant.objects.filter(sku_variant_code=item_sku).first()
                if not variant:
                    skipped += 1
                    continue
                updated = ProductVariantMarketplace.objects.filter(
                    product_variant=variant,
                    marketplace=marketplace,
                    product_variant__company=connection.company,
                ).update(shopee_item_id=item_id, shopee_model_id=0)
                if updated:
                    matched += updated
                else:
                    skipped += 1

        return {"matched": matched, "skipped": skipped, "errors": errors}
