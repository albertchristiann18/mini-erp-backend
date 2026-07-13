import logging
from typing import List, Optional

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import ProductVariant
from apps.marketplace.tiktok.client import TikTokAPIError, TikTokClient
from apps.marketplace.tiktok.models import TikTokShop
from apps.sales.models import SalesOrder
from apps.sales.services.sales_service import SalesOrderService

logger = logging.getLogger(__name__)

TIKTOK_TO_INTERNAL_STATUS = {
    "UNPAID": "PENDING",
    "ON_HOLD": "PENDING",
    "AWAITING_SHIPMENT": "CONFIRMED",
    "AWAITING_COLLECTION": "CONFIRMED",
    "IN_TRANSIT": "SHIPPING",
    "DELIVERED": "DELIVERED",
    "COMPLETED": "COMPLETED",
    "CANCELLED": "CANCELLED",
}


class TikTokOrderSyncer:
    def __init__(self, shop: TikTokShop):
        self.shop = shop
        self.client = TikTokClient(shop)

    def sync_orders(self, order_ids: Optional[List[str]] = None) -> int:
        """Fetch orders from TikTok and upsert as SalesOrders."""
        if order_ids:
            count = 0
            for order_id in order_ids:
                try:
                    response = self.client.get("/api/orders/detail", params={"order_id": order_id})
                    order_data = response.get("order", response)
                    if order_data:
                        result = self.upsert_order(order_data)
                        if result:
                            count += 1
                except TikTokAPIError as e:
                    logger.error(f"Failed to fetch TikTok order {order_id}: {e}")
            return count

        # Search all recent orders
        try:
            response = self.client.get("/api/orders/search")
        except TikTokAPIError as e:
            logger.error(f"Failed to search TikTok orders: {e}")
            return 0

        orders = response.get("orders", [])
        count = 0
        for order_data in orders:
            result = self.upsert_order(order_data)
            if result:
                count += 1
        return count

    @transaction.atomic
    def upsert_order(self, order_data: dict) -> Optional[SalesOrder]:
        """Create or skip a single TikTok order."""
        order_id = order_data.get("order_id", "")
        tiktok_status = order_data.get("status", "UNPAID")
        internal_status = TIKTOK_TO_INTERNAL_STATUS.get(tiktok_status, "PENDING")

        # Skip if already exists
        existing = SalesOrder.objects.filter(
            marketplace_order_id=order_id,
        ).first()

        if existing:
            service = SalesOrderService()
            try:
                service.update_sales_order(existing, {"status": internal_status})
            except Exception as e:
                logger.warning(f"Could not update order {order_id} status: {e}")
            return existing

        if not self.shop.warehouse:
            logger.error(f"Shop {self.shop.shop_id} has no warehouse — cannot create order")
            return None

        recipient = order_data.get("recipient_address", {})

        # Build items
        items_data = []
        for item in order_data.get("line_items", []):
            sku = item.get("seller_sku", "").strip()
            if not sku:
                continue
            variant = ProductVariant.objects.filter(sku_variant_code=sku).first()
            if not variant:
                logger.warning(f"Cannot find variant for TikTok SKU: {sku}")
                continue

            selling_price = item.get("sale_price", item.get("original_price", 0))
            quantity = item.get("quantity", 1)

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
            logger.warning(f"Order {order_id}: no matching variants found, skipping")
            return None

        so_data = {
            "company_id": str(self.shop.company.pk),
            "warehouse_id": str(self.shop.warehouse.pk),
            "marketplace_order_id": order_id,
            "marketplace_order_number": order_id,
            "status": internal_status,
            "customer_name": recipient.get("name", ""),
            "customer_phone": recipient.get("phone", ""),
            "shipping_address": recipient.get("full_address", ""),
            "shipping_city": recipient.get("city", ""),
            "shipping_province": recipient.get("state", ""),
            "courier_name": order_data.get("shipping_provider", ""),
            "tracking_number": order_data.get("tracking_number", ""),
            "order_date": timezone.now(),
            "shipping_fee": order_data.get("shipping_fee", 0),
            "items": items_data,
        }

        service = SalesOrderService()
        so = service.create_sales_order(so_data)
        logger.info(f"Created SalesOrder {so.order_number} from TikTok order {order_id}")
        return so
