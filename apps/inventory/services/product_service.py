import logging

from django.db import transaction

from apps.inventory.models import (
    Product,
    ProductVariant,
    ProductVariantMarketplace,
)

logger = logging.getLogger(__name__)


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
        listings_data_tupple = []
        variants = []
        variant_index = 0
        for i in range(len(data_list)):
            data = data_list[i]
            variants_data = data.pop("variants")
            for variant_data in variants_data:
                for listing_data in variant_data.get("marketplace_listings", []):
                    listings_data_tupple.append((variant_index, listing_data))

                variants.append(
                    ProductVariant(
                        product_id=product_index_map[i],
                        company_id=company_id,
                        name=variant_data.get("name", ""),
                        sku_variant_code=variant_data.get("sku_variant_code", ""),
                        variant_values=variant_data.get("variant_values", {}),
                        base_price=variant_data.get("base_price", 0),
                    )
                )
                variant_product_map.append(i)
                variant_index += 1

        created_variants = ProductVariant.objects.bulk_create(variants, batch_size=100)
        variant_index_map = {i: obj.id for i, obj in enumerate(created_variants)}
        create_listing_data = []
        for i, listing_data in listings_data_tupple:
            create_listing_data.append(
                ProductVariantMarketplace(
                    product_variant_id=variant_index_map[i],
                    company_id=company_id,
                    marketplace_id=listing_data["marketplace_id"],
                    selling_price=listing_data["selling_price"],
                    discounted_price=listing_data.get("discounted_price"),
                )
            )
        ProductVariantMarketplace.objects.bulk_create(create_listing_data, batch_size=100)

        created_variants_by_product: dict[int, list[ProductVariant]] = {
            i: [] for i in range(len(data_list))
        }
        for j, variant in enumerate(created_variants):
            product_idx = variant_product_map[j]
            created_variants_by_product[product_idx].append(variant)

        result = []
        for i, product in enumerate(created_products):
            result.append(
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
            )
        return result

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
