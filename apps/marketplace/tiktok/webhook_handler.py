import logging

from django.db import transaction

from apps.marketplace.tiktok.models import TikTokWebhookLog

logger = logging.getLogger(__name__)


class WebhookProcessor:
    def process(self, log: TikTokWebhookLog) -> None:
        """Dispatch webhook to the correct handler based on event type."""
        try:
            if log.event_type == "order.status_update":
                self._handle_order_update(log)
            elif log.event_type == "authorization.removed":
                self._handle_authorization_removed(log)
            else:
                logger.info(f"Unhandled webhook event_type={log.event_type}")

            log.processed = True
            log.error = ""
            log.save(update_fields=["processed", "error"])

        except Exception as e:
            logger.exception(f"Webhook processing failed for log {log.id}: {e}")
            log.error = str(e)
            log.save(update_fields=["error"])

    @transaction.atomic
    def _handle_order_update(self, log: TikTokWebhookLog) -> None:
        """Handle order.status_update webhook."""
        from apps.marketplace.tiktok.order_sync import TikTokOrderSyncer

        payload = log.payload
        order_id = payload.get("order_id") or payload.get("data", {}).get("order_id")
        new_status = payload.get("status") or payload.get("data", {}).get("status")

        if not order_id:
            logger.warning(f"Webhook {log.id}: no order_id in payload")
            return

        shop = log.shop
        if not shop:
            logger.warning(f"Webhook {log.id}: no shop linked")
            return

        if new_status == "AWAITING_SHIPMENT":
            syncer = TikTokOrderSyncer(shop)
            syncer.sync_orders(order_ids=[order_id])

        logger.info(f"Processed order webhook: order_id={order_id} status={new_status}")

    def _handle_authorization_removed(self, log: TikTokWebhookLog) -> None:
        """Deactivate shop when authorization is removed."""
        if log.shop:
            log.shop.is_active = False
            log.shop.save(update_fields=["is_active"])
            logger.info(f"Deactivated TikTok shop {log.shop.shop_id}")
