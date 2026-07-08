import logging
from datetime import (
    datetime,
    timedelta,
    timezone as dt_timezone,
)
from typing import Optional

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import ProductVariant
from apps.omnichannel.vendor.shopee.client import ShopeeAPIError, ShopeeClient
from apps.omnichannel.vendor.shopee.models import ShopeeShop
from apps.sales.models import SalesOrder
from apps.sales.services.sales_service import SalesOrderService

logger = logging.getLogger(__name__)

SHOPEE_TO_INTERNAL_STATUS = {
    "UNPAID": "PENDING",
    "READY_TO_SHIP": "CONFIRMED",
    "RETRY_SHIP": "CONFIRMED",
    "SHIPPED": "SHIPPING",
    "TO_CONFIRM_RECEIVE": "DELIVERED",
    "IN_CANCEL": "PENDING",
    "CANCELLED": "CANCELLED",
    "COMPLETED": "COMPLETED",
}


class ShopeeOrderSyncer:
    def __init__(self, shop: ShopeeShop):
        self.shop = shop
        self.client = ShopeeClient(shop)

    def sync_recent_orders(self, hours_back: int = 24) -> int:
        """Sync recent orders for this shop within the time window. Returns count of orders synced."""
        time_to = int(timezone.now().timestamp())
        time_from = int((timezone.now() - timedelta(hours=hours_back)).timestamp())

        count = 0
        cursor = ""

        while True:
            try:
                response = self.client.get_order_list(
                    time_range_field="create_time",
                    time_from=time_from,
                    time_to=time_to,
                    page_size=50,
                    cursor=cursor,
                )
            except ShopeeAPIError as e:
                logger.error(f"Failed to get order list for shop {self.shop.shop_id}: {e}")
                break

            order_list = response.get("order_list", [])
            order_sns = [o["order_sn"] for o in order_list if "order_sn" in o]

            if order_sns:
                for i in range(0, len(order_sns), 50):
                    batch = order_sns[i : i + 50]
                    try:
                        detail_response = self.client.get_order_detail(batch)
                        for order_data in detail_response.get("order_list", []):
                            self._upsert_order(order_data)
                            count += 1
                    except ShopeeAPIError as e:
                        logger.error(f"Failed to get order details: {e}")

            if not response.get("more", False):
                break
            cursor = response.get("next_cursor", "")
            if not cursor:
                break

        self.shop.last_order_sync_at = timezone.now()
        self.shop.save(update_fields=["last_order_sync_at"])
        return count

    @transaction.atomic
    def sync_order_by_sn(self, order_sn: str) -> Optional[SalesOrder]:
        """Fetch one order from Shopee and create/update it in our system."""
        try:
            response = self.client.get_order_detail([order_sn])
        except ShopeeAPIError as e:
            logger.error(f"Failed to fetch order {order_sn}: {e}")
            return None

        order_list = response.get("order_list", [])
        if not order_list:
            logger.warning(f"Order {order_sn} not found in Shopee response")
            return None

        return self._upsert_order(order_list[0])

    @transaction.atomic
    def _upsert_order(self, shopee_order: dict) -> Optional[SalesOrder]:
        """Create or update a SalesOrder from Shopee order data."""
        order_sn = shopee_order.get("order_sn", "")
        shopee_status = shopee_order.get("order_status", "UNPAID")
        internal_status = SHOPEE_TO_INTERNAL_STATUS.get(shopee_status, "PENDING")

        # Check if order already exists
        existing = SalesOrder.objects.filter(
            marketplace_order_id=order_sn,
            marketplace=self.shop.marketplace,
        ).first()

        if existing:
            self._update_order_status(existing, internal_status)
            return existing

        # Create new order
        if not self.shop.default_warehouse:
            logger.error(f"Shop {self.shop.shop_id} has no default_warehouse — cannot create order")
            return None

        order_date = datetime.fromtimestamp(shopee_order.get("create_time", 0), tz=dt_timezone.utc)

        recipient = shopee_order.get("recipient_address", {})

        # Build items
        items_data = []
        for item in shopee_order.get("item_list", []):
            variant = self._find_variant(item)
            if not variant:
                logger.warning(
                    f"Cannot find variant for Shopee item: {item.get('item_sku', '')} / model_sku={item.get('model_sku', '')}"
                )
                continue

            selling_price = item.get("model_discounted_price", item.get("model_original_price", 0))
            quantity = item.get("model_quantity_purchased", 1)

            items_data.append(
                {
                    "product_variant_id": str(variant.id),
                    "quantity": quantity,
                    "selling_price": selling_price,
                    "discount_amount": 0,
                    "commission_fee": 0,
                    "service_fee": 0,
                }
            )

        if not items_data:
            logger.warning(f"Order {order_sn}: no matching variants found, skipping")
            return None

        so_data = {
            "company_id": str(self.shop.company.pk),
            "warehouse_id": str(self.shop.default_warehouse.pk),
            "marketplace_id": str(self.shop.marketplace.pk) if self.shop.marketplace else None,
            "marketplace_order_id": order_sn,
            "marketplace_order_number": order_sn,
            "status": internal_status,
            "source_platform": SalesOrder.SourcePlatform.SHOPEE,
            "customer_name": recipient.get("name", ""),
            "customer_phone": recipient.get("phone", ""),
            "shipping_address": recipient.get("full_address", ""),
            "shipping_city": recipient.get("city", ""),
            "shipping_province": recipient.get("state", ""),
            "order_date": order_date,
            "courier_name": shopee_order.get("shipping_carrier", ""),
            "tracking_number": shopee_order.get("tracking_number", ""),
            "shipping_fee": shopee_order.get("actual_shipping_fee", 0),
            "items": items_data,
        }

        service = SalesOrderService()
        so = service.create_sales_order(so_data)
        logger.info(f"Created SalesOrder {so.order_number} from Shopee order {order_sn}")
        return so

    def _find_variant(self, shopee_item: dict) -> Optional[ProductVariant]:
        """Find our ProductVariant by matching SKU from Shopee item, scoped to this shop's company."""
        # Try model_sku first, then item_sku
        for sku_field in ["model_sku", "item_sku"]:
            sku = shopee_item.get(sku_field, "").strip()
            if sku:
                variant = ProductVariant.objects.filter(
                    sku_variant_code=sku,
                    company=self.shop.company,
                ).first()
                if variant:
                    return variant

        return None

    def _update_order_status(self, so: SalesOrder, new_status: str) -> None:
        """Update order status if it's a valid forward transition."""
        service = SalesOrderService()
        try:
            service.update_sales_order(so, {"status": new_status})
        except Exception as e:
            logger.warning(f"Could not transition order {so.order_number} to {new_status}: {e}")
