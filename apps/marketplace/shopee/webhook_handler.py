import logging

from django.db import transaction

from apps.marketplace.shopee.models import ShopeeShop, ShopeeWebhookLog

logger = logging.getLogger(__name__)

# Shopee webhook event codes
EVENT_ORDER_STATUS = 3
EVENT_SHOP_UPDATE = 4


class WebhookProcessor:
    def process(self, log: ShopeeWebhookLog) -> None:
        """Dispatch webhook to the correct handler based on event code."""
        try:
            if log.event_code == EVENT_ORDER_STATUS:
                self._handle_order_status(log)
            elif log.event_code == EVENT_SHOP_UPDATE:
                self._handle_shop_update(log)
            else:
                logger.info(f"Unhandled webhook event_code={log.event_code}")

            log.processed = True
            log.error_message = ""
            log.save(update_fields=["processed", "error_message"])

        except Exception as e:
            logger.exception(f"Webhook processing failed for log {log.id}: {e}")
            log.error_message = str(e)
            log.save(update_fields=["error_message"])

    @transaction.atomic
    def _handle_order_status(self, log: ShopeeWebhookLog) -> None:
        """Handle ORDER_STATUS_UPDATE webhook."""
        from apps.marketplace.shopee.order_sync import ShopeeOrderSyncer

        payload = log.payload
        order_sn = payload.get("ordersn") or payload.get("data", {}).get("ordersn")
        new_shopee_status = payload.get("status") or payload.get("data", {}).get("status")

        if not order_sn:
            logger.warning(f"Webhook {log.id}: no ordersn in payload")
            return

        try:
            shop = ShopeeShop.objects.get(shop_id=log.shop_id, is_active=True)
        except ShopeeShop.DoesNotExist:
            logger.warning(f"No active ShopeeShop for shop_id={log.shop_id}")
            return

        syncer = ShopeeOrderSyncer(shop)
        syncer.sync_order_by_sn(order_sn)

        logger.info(f"Processed order webhook: ordersn={order_sn} status={new_shopee_status}")

    def _handle_shop_update(self, log: ShopeeWebhookLog) -> None:
        """Refresh shop info when shop details change."""
        try:
            shop = ShopeeShop.objects.get(shop_id=log.shop_id, is_active=True)
            from apps.marketplace.shopee.client import ShopeeClient

            client = ShopeeClient(shop)
            info = client.get_shop_info()
            shop.shop_name = info.get("shop_name", shop.shop_name)
            shop.save(update_fields=["shop_name"])
        except ShopeeShop.DoesNotExist:
            pass
