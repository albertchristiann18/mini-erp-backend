import logging
from typing import Any

from django.db import transaction

from apps.catalog.models import (
    Product,
    ProductVariant,
)

logger = logging.getLogger(__name__)


def _build_variant_name(variant_values: dict[str, str]) -> str:
    return " / ".join(v for v in variant_values.values() if v) or "Default"


class ProductService:
    @transaction.atomic
    def create_product_with_variants(self, validated_data: list | dict) -> list[dict]:
        if isinstance(validated_data, dict):
            data_list = [validated_data]
        else:
            data_list = list(validated_data)

        company_id = data_list[0].get("company_id", "")

        supplier_info: list[tuple[str | None, str | None]] = []
        for data in data_list:
            supplier_info.append((data.pop("supplier_id", None), data.pop("supplier_link", None)))

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
                dim1_key=data.get("dim1_key", ""),
                dim2_key=data.get("dim2_key", ""),
                dim1_options=data.get("dim1_options", []),
                dim2_options=data.get("dim2_options", []),
            )
            for data in data_list
        ]
        created_products = Product.objects.bulk_create(products, batch_size=100)
        product_index_map = {i: obj.id for i, obj in enumerate(created_products)}

        sku_map: dict[str, str] = dict(
            Product.objects.filter(id__in=[p.id for p in created_products]).values_list(
                "id", "sku_code"
            )
        )

        variant_product_map: list[int] = []
        variants: list[ProductVariant] = []
        for i, data in enumerate(data_list):
            variants_data = data.pop("variants", [])
            if not variants_data:
                sku_code = sku_map.get(product_index_map[i], "SKU")
                variants.append(
                    ProductVariant(
                        product_id=product_index_map[i],
                        company_id=company_id,
                        name="Default",
                        sku_variant_code=f"{sku_code}-DEFAULT",
                        variant_values={},
                        base_price=0,
                    )
                )
                variant_product_map.append(i)
            else:
                sku_code = sku_map.get(product_index_map[i], "SKU")
                for variant_data in variants_data:
                    variant_values = variant_data.get("variant_values", {})
                    raw_suffix = variant_data.get("sku_variant_code", "")
                    final_sku = f"{sku_code}-{raw_suffix}" if raw_suffix else f"{sku_code}-VAR"
                    variants.append(
                        ProductVariant(
                            product_id=product_index_map[i],
                            company_id=company_id,
                            name=_build_variant_name(variant_values),
                            sku_variant_code=final_sku,
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

        from apps.inventory.models import ProductSupplier

        suppliers_to_create = []
        for i, (s_id, s_link) in enumerate(supplier_info):
            if s_id:
                suppliers_to_create.append(
                    ProductSupplier(
                        product_id=product_index_map[i],
                        supplier_id=s_id,
                        company_id=company_id,
                        supplier_link=s_link or None,
                    )
                )
        if suppliers_to_create:
            ProductSupplier.objects.bulk_create(suppliers_to_create, ignore_conflicts=True)

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
        variant_options: dict[str, list[str]],
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
            name = _build_variant_name(variant_values)

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
        from apps.catalog.models import Product
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
        from apps.catalog.models import ProductVariant

        variant = ProductVariant.objects.get(id=variant_id)
        variant.base_price = base_price
        variant.save(update_fields=["base_price", "udate"])
        return {"id": str(variant.id), "base_price": variant.base_price}

    def upsert_dimension_image(
        self,
        product: "Product",
        dim_key: str,
        dim_value: str,
        photo_file: Any,
    ) -> Any:
        from apps.inventory.models import ProductDimensionImage

        allowed_keys = {k for k in [product.dim1_key, product.dim2_key] if k}
        if allowed_keys and dim_key not in allowed_keys:
            raise ValueError(
                f"dim_key '{dim_key}' is not a configured dimension for this product "
                f"(allowed: {', '.join(sorted(allowed_keys))})"
            )

        existing = ProductDimensionImage.objects.filter(
            product=product, dim_key=dim_key, dim_value=dim_value
        ).first()
        if existing:
            if existing.photo:
                existing.photo.delete(save=False)
            existing.photo = photo_file
            existing.save(update_fields=["photo", "udate"])
            return existing
        return ProductDimensionImage.objects.create(
            product=product,
            company_id=product.company_id,  # type: ignore[attr-defined]
            dim_key=dim_key,
            dim_value=dim_value,
            photo=photo_file,
        )

    def delete_dimension_image(
        self,
        product: "Product",
        dim_key: str,
        dim_value: str,
    ) -> None:
        from apps.inventory.models import ProductDimensionImage

        dim_img = ProductDimensionImage.objects.filter(
            product=product, dim_key=dim_key, dim_value=dim_value
        ).first()
        if dim_img is None:
            raise ValueError("Dimension image not found")
        if dim_img.photo:
            dim_img.photo.delete(save=False)
        dim_img.delete()

    def cleanup_orphan_dimension_images(
        self, product_id: str, old_dim1_key: str, old_dim2_key: str
    ) -> None:
        from apps.catalog.models import Product
        from apps.inventory.models import ProductDimensionImage

        try:
            product = Product.objects.get(id=product_id)
        except Product.DoesNotExist:
            return

        keys_to_clean: set[str] = set()
        if old_dim1_key and old_dim1_key != product.dim1_key:
            keys_to_clean.add(old_dim1_key)
        if old_dim2_key and old_dim2_key != product.dim2_key:
            keys_to_clean.add(old_dim2_key)

        if not keys_to_clean:
            return

        orphans = list(
            ProductDimensionImage.objects.filter(product=product, dim_key__in=keys_to_clean)
        )
        for orphan in orphans:
            if orphan.photo:
                orphan.photo.delete(save=False)
        ProductDimensionImage.objects.filter(product=product, dim_key__in=keys_to_clean).delete()

    def _trigger_shopee_price_update(self, listing_ids: list[str], company_id: str) -> None:
        from apps.catalog.models import ProductVariantMarketplace
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
