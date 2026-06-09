import logging
from typing import Any

from django.db import transaction

from apps.inventory.models import (
    Product,
    ProductVariant,
)

logger = logging.getLogger(__name__)


def _build_variant_name(variant_values: dict[str, str], variant_options: list[dict]) -> str:
    sorted_opts = sorted(variant_options, key=lambda o: o.get("order", 0))
    parts: list[str] = []
    for opt in sorted_opts:
        val_id = variant_values.get(opt["id"], "").strip()
        if not val_id:
            continue
        label = next(
            (v["label"] for v in opt.get("values", []) if v["id"] == val_id),
            val_id,
        )
        parts.append(label)
    return " / ".join(parts) if parts else "Default"


class ProductService:
    @transaction.atomic
    def create_product_with_variants(self, validated_data: list | dict) -> list[dict]:
        if isinstance(validated_data, dict):
            data_list = [validated_data]
        else:
            data_list = list(validated_data)

        company_id = data_list[0].get("company_id", "")

        products = [
            Product(
                company_id=company_id,
                category_id=data.get("category_id", ""),
                name=data.get("name", ""),
                description=data.get("description", ""),
                variant_options=data.get("variant_options", []),
                specifications=data.get("specifications", {}),
                weight=data.get("weight", 0),
                length=data.get("length", 0),
                width=data.get("width", 0),
                height=data.get("height", 0),
            )
            for data in data_list
        ]
        created_products = Product.objects.bulk_create(products, batch_size=100)
        product_index_map = {i: obj.id for i, obj in enumerate(created_products)}

        variant_product_map: list[int] = []
        variants: list[ProductVariant] = []
        for i, data in enumerate(data_list):
            variants_data = data.pop("variants", [])
            variant_options_data = data.get("variant_options", [])
            for variant_data in variants_data:
                variant_values = variant_data.get("variant_values", {})
                variants.append(
                    ProductVariant(
                        product_id=product_index_map[i],
                        company_id=company_id,
                        name=_build_variant_name(variant_values, variant_options_data),
                        sku_variant_code=variant_data.get("sku_variant_code", ""),
                        variant_values=variant_values,
                        base_price=variant_data.get("base_price", 0),
                    )
                )
                variant_product_map.append(i)

        created_variants = ProductVariant.objects.bulk_create(variants, batch_size=100)

        created_variants_by_product: dict[int, list[ProductVariant]] = {
            i: [] for i in range(len(data_list))
        }
        for j, variant in enumerate(created_variants):
            created_variants_by_product[variant_product_map[j]].append(variant)

        return [
            {
                "id": str(product.id),
                "name": product.name,
                "variants": [
                    {
                        "id": str(v.id),
                        "name": v.name,
                        "sku_variant_code": v.sku_variant_code,
                    }
                    for v in created_variants_by_product[i]
                ],
            }
            for i, product in enumerate(created_products)
        ]

    @transaction.atomic
    def save_variants(
        self,
        product_id: str,
        company_id: str,
        variant_options: list[dict],
        variants: list[dict],
    ) -> dict[str, Any]:
        product = Product.objects.select_for_update().get(id=product_id, company_id=company_id)
        product.variant_options = variant_options
        product.save(update_fields=["variant_options", "udate"])

        incoming_ids: set[str] = set()
        created_count = 0
        updated_count = 0

        for v_data in variants:
            variant_id = (v_data.get("id") or "").strip()
            variant_values = v_data.get("variant_values", {})
            sku_variant_code = v_data.get("sku_variant_code", "")
            base_price = v_data.get("base_price", 0)
            name = _build_variant_name(variant_values, variant_options)

            if variant_id:
                try:
                    variant = ProductVariant.objects.select_for_update().get(
                        id=variant_id, product_id=product_id, company_id=company_id
                    )
                    variant.name = name
                    variant.base_price = base_price
                    if sku_variant_code:
                        variant.sku_variant_code = sku_variant_code
                    variant.is_active = True
                    variant.save(
                        update_fields=[
                            "name",
                            "base_price",
                            "sku_variant_code",
                            "is_active",
                            "udate",
                        ]
                    )
                    incoming_ids.add(str(variant.id))
                    updated_count += 1
                except ProductVariant.DoesNotExist:
                    new_variant = ProductVariant.objects.create(
                        product_id=product_id,
                        company_id=company_id,
                        name=name,
                        variant_values=variant_values,
                        sku_variant_code=sku_variant_code,
                        base_price=base_price,
                    )
                    incoming_ids.add(str(new_variant.id))
                    created_count += 1
            else:
                new_variant = ProductVariant.objects.create(
                    product_id=product_id,
                    company_id=company_id,
                    name=name,
                    variant_values=variant_values,
                    sku_variant_code=sku_variant_code,
                    base_price=base_price,
                )
                incoming_ids.add(str(new_variant.id))
                created_count += 1

        stale_qs = ProductVariant.objects.filter(
            product_id=product_id, company_id=company_id, is_active=True
        ).exclude(id__in=list(incoming_ids))

        deactivated: list[str] = []
        kept_with_stock: list[str] = []
        for v in stale_qs.select_for_update():
            if v.total_incoming_qty == 0 and v.total_available_qty == 0:
                v.is_active = False
                v.save(update_fields=["is_active", "udate"])
                deactivated.append(str(v.id))
            else:
                kept_with_stock.append(str(v.id))

        return {
            "product_id": str(product.id),
            "created": created_count,
            "updated": updated_count,
            "deactivated": deactivated,
            "kept_with_stock": kept_with_stock,
        }

    def _trigger_shopee_product_update(self, product_id: str) -> None:
        from apps.inventory.models import Product
        from apps.omnichannel.vendor.shopee.product_push import ShopeeProductPushService
        from core.models import MarketplaceConnection

        try:
            product = Product.objects.select_related("category").get(id=product_id)
        except Product.DoesNotExist:
            return

        connections = MarketplaceConnection.objects.filter(
            platform="SHOPEE",
            is_active=True,
            company=product.company,
        ).select_related("shopee_shop")

        if not connections.exists():
            return

        service = ShopeeProductPushService()
        for connection in connections:
            if not connection.shopee_shop:
                continue
            try:
                service.update_product(product, connection.shopee_shop)
            except Exception:
                logger.warning(
                    "Shopee product update trigger failed for product %s on shop %s",
                    product_id,
                    connection.shopee_shop.shop_id,
                    exc_info=True,
                )

    def update_variant_base_price(self, variant_id: str, base_price: int) -> dict[str, str | int]:
        from apps.inventory.models import ProductVariant

        variant = ProductVariant.objects.get(id=variant_id)
        variant.base_price = base_price
        variant.save(update_fields=["base_price", "udate"])
        return {"id": str(variant.id), "base_price": variant.base_price}

    def _trigger_shopee_price_update(self, listing_ids: list[str], company_id: str) -> None:
        from apps.inventory.models import ProductVariantMarketplace
        from apps.omnichannel.vendor.shopee.product_push import ShopeeProductPushService
        from core.models import MarketplaceConnection

        connections = MarketplaceConnection.objects.filter(
            platform="SHOPEE",
            is_active=True,
            company_id=company_id,
        ).select_related("shopee_shop")

        if not connections.exists():
            return

        service = ShopeeProductPushService()
        for connection in connections:
            if not connection.shopee_shop:
                continue
            listings = ProductVariantMarketplace.objects.filter(
                id__in=listing_ids,
                marketplace=connection.shopee_shop.marketplace,
                is_active=True,
                shopee_item_id__isnull=False,
                shopee_model_id__isnull=False,
            )
            for listing in listings:
                try:
                    service.update_price_for_listing(listing, connection.shopee_shop)
                except Exception:
                    logger.warning(
                        "Shopee price update trigger failed for listing %s on shop %s",
                        listing.id,
                        connection.shopee_shop.shop_id,
                        exc_info=True,
                    )
