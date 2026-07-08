import logging
from typing import Any

from apps.omnichannel.vendor.shopee.client import ShopeeClient
from apps.omnichannel.vendor.shopee.exceptions import ShopeeAPIError
from apps.omnichannel.vendor.shopee.models import ShopeeShop

logger = logging.getLogger(__name__)


class ShopeeProductPushService:
    def push_product(self, product: Any, shop: ShopeeShop) -> dict[str, Any]:
        from apps.catalog.models import ProductVariantMarketplace

        errors: list[str] = []

        # --- Guard: category must have shopee_category_id ---
        if not getattr(product.category, "shopee_category_id", None):
            return {
                "item_id": None,
                "models_pushed": 0,
                "errors": [f"Category '{product.category.name}' has no shopee_category_id set"],
            }

        marketplace = shop.marketplace
        if not marketplace:
            return {
                "item_id": None,
                "models_pushed": 0,
                "errors": ["ShopeeShop has no linked Marketplace"],
            }

        # --- Get all active listings for this product on this shop's marketplace ---
        listings = list(
            ProductVariantMarketplace.objects.filter(
                product_variant__product=product,
                marketplace=marketplace,
                is_active=True,
            ).select_related("product_variant")
        )
        if not listings:
            return {
                "item_id": None,
                "models_pushed": 0,
                "errors": ["No active ProductVariantMarketplace listings found"],
            }

        client = ShopeeClient(shop)

        # --- Logistics: fetch enabled channels from Shopee ---
        logistics: list[dict[str, Any]] = []
        try:
            channel_resp = client.get_channel_list()
            for ch in channel_resp.get("logistics_channel_list", []):
                if ch.get("enabled"):
                    logistics.append({"logistic_id": ch["logistics_channel_id"], "enabled": True})
        except ShopeeAPIError as e:
            errors.append(f"get_channel_list failed: {e} — using empty logistics")

        # --- Upload primary image ---
        images: list[dict[str, str]] = []
        try:
            if product.product_photo:
                product.product_photo.open("rb")
                image_id = client.upload_image(product.product_photo)
                product.product_photo.close()
                if image_id:
                    images = [{"image_id": image_id}]
        except Exception as e:
            errors.append(f"Image upload failed: {e} — proceeding without image")

        # --- Build base item payload ---
        base_payload: dict[str, Any] = {
            "item_name": product.name,
            "description": product.description or product.name,
            "item_sku": product.sku_code,
            "category_id": product.category.shopee_category_id,
            "weight": max(product.weight, 1),  # Shopee requires > 0, in grams
            "condition": "NEW",
            "logistics": logistics,
        }
        if images:
            base_payload["images"] = images
        if product.length and product.width and product.height:
            base_payload["dimension"] = {
                "package_length": product.length,
                "package_width": product.width,
                "package_height": product.height,
            }

        # --- Single variant vs multi-variant ---
        is_single = len(listings) == 1 and not product.variant_options

        try:
            if is_single:
                listing = listings[0]
                variant = listing.product_variant
                payload = {
                    **base_payload,
                    "has_model": False,
                    "original_price": listing.selling_price,
                    "normal_stock": max(variant.total_available_qty, 0),
                }
                resp = client.add_item(payload)
                item_id: int = resp["item_id"]
                ProductVariantMarketplace.objects.filter(pk=listing.pk).update(
                    shopee_item_id=item_id, shopee_model_id=0
                )
                return {"item_id": item_id, "models_pushed": 1, "errors": errors}

            else:
                # Build tier_variation from new variant_options schema
                sorted_opts = sorted(product.variant_options, key=lambda o: o.get("order", 0))
                tier_option_sets: list[list[str]] = [[] for _ in sorted_opts]

                def _get_label(opt: dict, val_id: str) -> str:
                    return next(
                        (v["label"] for v in opt.get("values", []) if v["id"] == val_id),
                        val_id,
                    )

                for listing in listings:
                    vv = listing.product_variant.variant_values or {}
                    for i, opt in enumerate(sorted_opts):
                        val_id = str(vv.get(opt["id"], "")).strip()
                        if val_id:
                            label = _get_label(opt, val_id)
                            if label and label not in tier_option_sets[i]:
                                tier_option_sets[i].append(label)

                tier_variation = [
                    {"name": opt["name"], "option_list": [{"option": v} for v in opts]}
                    for opt, opts in zip(sorted_opts, tier_option_sets)
                    if opts
                ]

                payload = {
                    **base_payload,
                    "has_model": True,
                    "tier_variation": tier_variation,
                }
                resp = client.add_item(payload)
                item_id = resp["item_id"]

                # Build models list for add_model
                models_payload: list[dict[str, Any]] = []
                listing_by_sku: dict[str, Any] = {
                    l.product_variant.sku_variant_code: l for l in listings
                }

                for listing in listings:
                    vv = listing.product_variant.variant_values or {}
                    tier_index: list[int] = []
                    valid = True
                    for i, (opt, opts) in enumerate(zip(sorted_opts, tier_option_sets)):
                        val_id = str(vv.get(opt["id"], "")).strip()
                        label = _get_label(opt, val_id) if val_id else ""
                        if label in opts:
                            tier_index.append(opts.index(label))
                        else:
                            valid = False
                            errors.append(
                                f"Variant {listing.product_variant.sku_variant_code} missing option {opt['name']}"
                            )
                            break
                    if not valid:
                        continue
                    models_payload.append(
                        {
                            "tier_index": tier_index,
                            "model_sku": listing.product_variant.sku_variant_code,
                            "original_price": listing.selling_price,
                            "normal_stock": max(listing.product_variant.total_available_qty, 0),
                        }
                    )

                if not models_payload:
                    return {
                        "item_id": item_id,
                        "models_pushed": 0,
                        "errors": errors + ["No valid models to push"],
                    }

                model_resp = client.add_model(item_id, models_payload)
                pushed = 0
                for model_data in model_resp.get("model", []):
                    model_id = model_data.get("model_id")
                    model_sku = model_data.get("model_sku", "")
                    if model_sku in listing_by_sku and model_id:
                        ProductVariantMarketplace.objects.filter(
                            pk=listing_by_sku[model_sku].pk
                        ).update(shopee_item_id=item_id, shopee_model_id=model_id)
                        pushed += 1
                return {"item_id": item_id, "models_pushed": pushed, "errors": errors}

        except ShopeeAPIError as e:
            errors.append(f"Shopee API error: {e}")
            return {"item_id": None, "models_pushed": 0, "errors": errors}
        except Exception as e:
            logger.exception(
                "push_product failed for product %s shop %s", product.sku_code, shop.shop_id
            )
            errors.append(f"Unexpected error: {e}")
            return {"item_id": None, "models_pushed": 0, "errors": errors}

    def update_product(self, product: Any, shop: ShopeeShop) -> dict[str, Any]:
        from apps.catalog.models import ProductVariantMarketplace

        errors: list[str] = []
        marketplace = shop.marketplace
        if not marketplace:
            return {"updated": False, "errors": ["ShopeeShop has no linked Marketplace"]}

        listing_with_id = ProductVariantMarketplace.objects.filter(
            product_variant__product=product,
            marketplace=marketplace,
            is_active=True,
            shopee_item_id__isnull=False,
        ).first()
        if not listing_with_id:
            return {
                "updated": False,
                "errors": ["No shopee_item_id found — use push_product first"],
            }

        assert listing_with_id.shopee_item_id is not None
        item_id: int = listing_with_id.shopee_item_id
        client = ShopeeClient(shop)

        images: list[dict[str, str]] = []
        try:
            if product.product_photo:
                product.product_photo.open("rb")
                image_id = client.upload_image(product.product_photo)
                product.product_photo.close()
                if image_id:
                    images = [{"image_id": image_id}]
        except Exception as e:
            errors.append(f"Image upload failed: {e}")

        payload: dict[str, Any] = {
            "item_name": product.name,
            "description": product.description or product.name,
            "item_sku": product.sku_code,
            "weight": max(product.weight, 1),
        }
        if images:
            payload["images"] = images
        if product.length and product.width and product.height:
            payload["dimension"] = {
                "package_length": product.length,
                "package_width": product.width,
                "package_height": product.height,
            }

        try:
            client.update_item(item_id, payload)
            return {"updated": True, "errors": errors}
        except ShopeeAPIError as e:
            errors.append(f"Shopee API error: {e}")
            return {"updated": False, "errors": errors}
        except Exception as e:
            logger.exception(
                "update_product failed for product %s shop %s", product.sku_code, shop.shop_id
            )
            errors.append(f"Unexpected error: {e}")
            return {"updated": False, "errors": errors}

    def update_price_for_listing(self, listing: Any, shop: ShopeeShop) -> dict[str, Any]:
        if not listing.shopee_item_id:
            return {"updated": False, "errors": ["No shopee_item_id — run match or push first"]}
        if listing.shopee_model_id is None:
            return {"updated": False, "errors": ["shopee_model_id is None"]}

        client = ShopeeClient(shop)
        price_list = [
            {
                "model_id": listing.shopee_model_id,
                "original_price": listing.selling_price,
            }
        ]
        try:
            client.update_price(listing.shopee_item_id, price_list)
            return {"updated": True, "errors": []}
        except ShopeeAPIError as e:
            return {"updated": False, "errors": [f"Shopee API error: {e}"]}
        except Exception as e:
            logger.exception(
                "update_price_for_listing failed for listing %s shop %s",
                listing.id,
                shop.shop_id,
            )
            return {"updated": False, "errors": [f"Unexpected error: {e}"]}
