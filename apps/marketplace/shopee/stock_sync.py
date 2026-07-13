import logging
from collections import defaultdict
from typing import Any

from apps.marketplace.shopee.client import ShopeeClient
from apps.marketplace.shopee.exceptions import ShopeeAPIError
from apps.marketplace.shopee.models import ShopeeShop, ShopeeStockSyncLog

logger = logging.getLogger(__name__)


class ShopeeStockSyncService:
    """
    ERP -> Shopee stock sync. ERP is always the source of truth.
    Never raises -- all failures are logged to ShopeeStockSyncLog.
    """

    def sync_single_variant(
        self,
        variant_id: str,
        shop: ShopeeShop,
    ) -> bool:
        from apps.catalog.models import ProductVariant, ProductVariantMarketplace

        try:
            variant = ProductVariant.objects.select_related("company").get(
                id=variant_id,
            )
            if variant.is_fake:
                return False

            listing = ProductVariantMarketplace.objects.filter(
                product_variant=variant,
                marketplace=shop.marketplace,
                is_active=True,
            ).first()
            if not listing:
                return False
            if not listing.shopee_item_id or not listing.shopee_model_id:
                return False

            qty = self._get_available_qty(variant_id)

            client = ShopeeClient(shop)
            payload = self._build_stock_payload(
                listing.shopee_item_id,
                listing.shopee_model_id,
                qty,
            )
            response = client.update_stock(payload["item_id"], payload["stock_list"])

            self._write_log(
                shop=shop,
                variant_id=variant_id,
                sku=variant.sku_variant_code,
                qty=qty,
                success=True,
                sync_type=ShopeeStockSyncLog.SyncType.SINGLE,
                shopee_item_id=listing.shopee_item_id,
                shopee_model_id=listing.shopee_model_id,
                shopee_response=response,
            )
            return True

        except ProductVariant.DoesNotExist:
            return False
        except ShopeeAPIError as e:
            variant_obj = ProductVariant.objects.filter(id=variant_id).first()
            self._write_log(
                shop=shop,
                variant_id=variant_id,
                sku=variant_obj.sku_variant_code if variant_obj else "",
                qty=0,
                success=False,
                sync_type=ShopeeStockSyncLog.SyncType.SINGLE,
                error_message=str(e),
            )
            return False
        except Exception as e:
            variant_obj = ProductVariant.objects.filter(id=variant_id).first()
            self._write_log(
                shop=shop,
                variant_id=variant_id,
                sku=variant_obj.sku_variant_code if variant_obj else "",
                qty=0,
                success=False,
                sync_type=ShopeeStockSyncLog.SyncType.SINGLE,
                error_message=str(e),
            )
            return False

    def sync_all_variants(self, shop: ShopeeShop) -> dict[str, Any]:
        from apps.catalog.models import ProductVariantMarketplace

        if not shop.marketplace:
            return {"success": 0, "failed": 0, "errors": []}

        listings = ProductVariantMarketplace.objects.filter(
            marketplace=shop.marketplace,
            is_active=True,
            shopee_item_id__isnull=False,
            shopee_model_id__isnull=False,
        ).select_related("product_variant")

        by_item: dict[int, list[dict]] = defaultdict(list)
        for listing in listings:
            variant = listing.product_variant
            if variant.is_fake:
                continue
            item_id: int = listing.shopee_item_id  # type: ignore[assignment]
            model_id: int = listing.shopee_model_id  # type: ignore[assignment]
            qty = self._get_available_qty(str(variant.id))
            by_item[item_id].append(
                {
                    "model_id": model_id,
                    "normal_stock": qty,
                    "variant_id": str(variant.id),
                    "sku": variant.sku_variant_code,
                    "shopee_item_id": item_id,
                    "shopee_model_id": model_id,
                }
            )

        success_count = 0
        failed_count = 0
        errors: list[dict[str, str]] = []

        client = ShopeeClient(shop)

        for item_id, variants in by_item.items():
            for i in range(0, len(variants), 50):
                chunk = variants[i : i + 50]
                stock_list = [
                    {"model_id": v["model_id"], "normal_stock": v["normal_stock"]} for v in chunk
                ]
                try:
                    response = client.update_stock(item_id, stock_list)
                    for v in chunk:
                        self._write_log(
                            shop=shop,
                            variant_id=v["variant_id"],
                            sku=v["sku"],
                            qty=v["normal_stock"],
                            success=True,
                            sync_type=ShopeeStockSyncLog.SyncType.FULL,
                            shopee_item_id=v["shopee_item_id"],
                            shopee_model_id=v["shopee_model_id"],
                            shopee_response=response,
                        )
                    success_count += len(chunk)
                except ShopeeAPIError as e:
                    failed_count += len(chunk)
                    for v in chunk:
                        self._write_log(
                            shop=shop,
                            variant_id=v["variant_id"],
                            sku=v["sku"],
                            qty=v["normal_stock"],
                            success=False,
                            sync_type=ShopeeStockSyncLog.SyncType.FULL,
                            error_message=str(e),
                        )
                    errors.extend([{"sku": v["sku"], "error": str(e)} for v in chunk])
                except Exception as e:
                    failed_count += len(chunk)
                    for v in chunk:
                        self._write_log(
                            shop=shop,
                            variant_id=v["variant_id"],
                            sku=v["sku"],
                            qty=v["normal_stock"],
                            success=False,
                            sync_type=ShopeeStockSyncLog.SyncType.FULL,
                            error_message=str(e),
                        )
                    errors.extend([{"sku": v["sku"], "error": str(e)} for v in chunk])

        return {
            "success": success_count,
            "failed": failed_count,
            "errors": errors,
        }

    def sync_batch(self, variant_ids: list[str], shop: ShopeeShop) -> dict[str, Any]:
        from apps.catalog.models import ProductVariantMarketplace

        if not variant_ids:
            return {"synced": 0, "failed": 0, "failed_variant_ids": []}

        try:
            listings = ProductVariantMarketplace.objects.filter(
                product_variant_id__in=variant_ids,
                marketplace=shop.marketplace,
                is_active=True,
                shopee_item_id__isnull=False,
                shopee_model_id__isnull=False,
            ).select_related("product_variant")

            by_item: dict[int, list[dict]] = defaultdict(list)
            for listing in listings:
                variant = listing.product_variant
                if variant.is_fake:
                    continue
                qty = self._get_available_qty(str(variant.id))
                item_id: int = listing.shopee_item_id  # type: ignore[assignment]
                by_item[item_id].append(
                    {
                        "model_id": listing.shopee_model_id,
                        "normal_stock": qty,
                        "variant_id": str(variant.id),
                        "sku": variant.sku_variant_code,
                        "shopee_item_id": listing.shopee_item_id,
                        "shopee_model_id": listing.shopee_model_id,
                    }
                )

            synced_count = 0
            failed_count = 0
            failed_variant_ids: list[str] = []

            client = ShopeeClient(shop)

            for item_id, variants in by_item.items():
                for i in range(0, len(variants), 50):
                    chunk = variants[i : i + 50]
                    stock_list = [
                        {"model_id": v["model_id"], "normal_stock": v["normal_stock"]}
                        for v in chunk
                    ]
                    try:
                        response = client.update_stock(item_id, stock_list)
                        for v in chunk:
                            self._write_log(
                                shop=shop,
                                variant_id=v["variant_id"],
                                sku=v["sku"],
                                qty=v["normal_stock"],
                                success=True,
                                sync_type=ShopeeStockSyncLog.SyncType.FULL,
                                shopee_item_id=v["shopee_item_id"],
                                shopee_model_id=v["shopee_model_id"],
                                shopee_response=response,
                            )
                        synced_count += len(chunk)
                    except ShopeeAPIError as e:
                        failed_count += len(chunk)
                        for v in chunk:
                            self._write_log(
                                shop=shop,
                                variant_id=v["variant_id"],
                                sku=v["sku"],
                                qty=v["normal_stock"],
                                success=False,
                                sync_type=ShopeeStockSyncLog.SyncType.FULL,
                                error_message=str(e),
                            )
                            failed_variant_ids.append(v["variant_id"])

            return {
                "synced": synced_count,
                "failed": failed_count,
                "failed_variant_ids": failed_variant_ids,
            }
        except Exception:
            logger.exception(
                "sync_batch failed for shop %s variants %s",
                shop.shop_id,
                variant_ids,
            )
            return {
                "synced": 0,
                "failed": len(variant_ids),
                "failed_variant_ids": variant_ids,
            }

    def _get_available_qty(self, variant_id: str) -> int:
        from apps.inventory.models import ProductVariantWarehouse

        stocks = ProductVariantWarehouse.objects.filter(
            product_variant_id=variant_id,
            warehouse__is_marketplace_visible=True,
        )
        total = sum(max(0, s.physical_qty - s.checkout_qty) for s in stocks)
        return max(0, total)

    def _build_stock_payload(
        self,
        item_id: int,
        model_id: int,
        qty: int,
    ) -> dict[str, Any]:
        return {
            "item_id": item_id,
            "stock_list": [{"model_id": model_id, "normal_stock": qty}],
        }

    def _write_log(
        self,
        shop: ShopeeShop,
        variant_id: str | None,
        sku: str,
        qty: int,
        success: bool,
        sync_type: str,
        error_message: str = "",
        shopee_item_id: int | None = None,
        shopee_model_id: int | None = None,
        shopee_response: dict | None = None,
    ) -> None:
        try:
            ShopeeStockSyncLog.objects.create(
                company=shop.company,
                shop=shop,
                variant_id=variant_id,
                sku_variant_code=sku,
                quantity_synced=qty,
                success=success,
                sync_type=sync_type,
                error_message=error_message,
                shopee_item_id=shopee_item_id,
                shopee_model_id=shopee_model_id,
                shopee_response=shopee_response or {},
            )
        except Exception:
            logger.exception(
                "Failed to write ShopeeStockSyncLog for variant %s",
                sku,
            )
