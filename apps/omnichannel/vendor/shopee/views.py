import hashlib
import hmac
import json
import logging

from django.db.models import QuerySet
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from apps.omnichannel.vendor.shopee.models import ShopeeShop, ShopeeSyncLog, ShopeeWebhookLog
from apps.omnichannel.vendor.shopee.serializers import (
    ShopeeShopSerializer,
    ShopeeSyncLogSerializer,
    ShopeeWebhookLogSerializer,
)
from apps.omnichannel.vendor.shopee.webhook_handler import WebhookProcessor
from core.permissions import IsStaffOrReadOnly

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def shopee_webhook(request: HttpRequest) -> JsonResponse:
    """
    Receive Shopee webhook events.
    URL: POST /shopee/webhook/
    """
    raw_body = request.body

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid json"}, status=400)

    shop_id = payload.get("shop_id") or payload.get("shopid")
    event_code = payload.get("code", 0)

    shopee_signature = request.headers.get("Authorization", "")

    log = ShopeeWebhookLog.objects.create(
        shop_id=shop_id or 0,
        event_code=event_code,
        payload=payload,
        signature=shopee_signature,
        processed=False,
    )

    # Verify signature if we have the shop
    if shop_id:
        try:
            shop = ShopeeShop.objects.get(shop_id=shop_id, is_active=True)
            expected = hmac.new(shop.partner_key.encode(), raw_body, hashlib.sha256).hexdigest()
            if shopee_signature and not hmac.compare_digest(expected, shopee_signature):
                logger.warning(f"Webhook signature mismatch for shop {shop_id}")
        except ShopeeShop.DoesNotExist:
            logger.warning(f"Webhook received for unknown shop_id={shop_id}")

    processor = WebhookProcessor()
    processor.process(log)

    return JsonResponse({"message": "ok"})


class ShopeeShopViewSet(viewsets.ModelViewSet):
    serializer_class = ShopeeShopSerializer
    permission_classes = [IsStaffOrReadOnly]

    def get_queryset(self) -> QuerySet[ShopeeShop]:
        user = self.request.user
        if user.is_authenticated:
            profile = getattr(user, "profile", None)
            if profile:
                return ShopeeShop.objects.filter(company=profile.company)
        return ShopeeShop.objects.all()

    @action(detail=True, methods=["post"], url_path="refresh-token")
    def refresh_token(self, request: Request, pk: str | None = None) -> Response:
        shop = self.get_object()
        from apps.omnichannel.vendor.shopee.client import ShopeeAPIError, ShopeeClient

        try:
            client = ShopeeClient(shop)
            client.refresh_access_token()
            return Response({"message": "Token refreshed"})
        except ShopeeAPIError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"], url_path="sync-orders")
    def sync_orders(self, request: Request, pk: str | None = None) -> Response:
        shop = self.get_object()
        from apps.omnichannel.vendor.shopee.order_sync import ShopeeOrderSyncer

        try:
            count = ShopeeOrderSyncer(shop).sync_recent_orders()
            return Response({"synced": count})
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=["post"], url_path="sync-stock")
    def sync_stock(self, request: Request, pk: str | None = None) -> Response:
        shop = self.get_object()
        from apps.omnichannel.vendor.shopee.stock_sync import ShopeeStockSyncService

        try:
            service = ShopeeStockSyncService()
            result = service.sync_all_variants(shop)
            return Response({"updated": result["success"]})
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=["post"], url_path="shipping-document")
    def shipping_document(self, request: Request, pk: str | None = None) -> Response:
        shop = self.get_object()
        order_sns = request.data.get("order_sns", [])
        if not isinstance(order_sns, list) or not order_sns:
            return Response(
                {"error": "order_sns must be a non-empty list"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from apps.omnichannel.vendor.shopee.client import ShopeeAPIError, ShopeeClient

        try:
            client = ShopeeClient(shop)
            result = client.get_shipping_document_result(order_sns)
            return Response(result)
        except ShopeeAPIError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ShopeeWebhookLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ShopeeWebhookLogSerializer
    permission_classes = [IsStaffOrReadOnly]

    def get_queryset(self) -> QuerySet[ShopeeWebhookLog]:
        user = self.request.user
        if user.is_authenticated:
            profile = getattr(user, "profile", None)
            if profile:
                qs = ShopeeWebhookLog.objects.filter(
                    shop_id__in=ShopeeShop.objects.filter(company=profile.company).values_list(
                        "shop_id", flat=True
                    )
                ).order_by("-cdate")
            else:
                qs = ShopeeWebhookLog.objects.all().order_by("-cdate")
        else:
            qs = ShopeeWebhookLog.objects.all().order_by("-cdate")
        shop_id = self.request.query_params.get("shop_id")
        processed = self.request.query_params.get("processed")
        if shop_id:
            qs = qs.filter(shop_id=shop_id)
        if processed is not None:
            qs = qs.filter(processed=processed.lower() == "true")
        return qs


class ShopeeSyncLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ShopeeSyncLog.objects.all().order_by("-started_at")
    serializer_class = ShopeeSyncLogSerializer
    permission_classes = [IsStaffOrReadOnly]
