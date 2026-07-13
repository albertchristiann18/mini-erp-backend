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

from apps.marketplace.tiktok.models import TikTokShop, TikTokWebhookLog
from apps.marketplace.tiktok.serializers import (
    TikTokShopSerializer,
    TikTokWebhookLogSerializer,
)
from apps.marketplace.tiktok.webhook_handler import WebhookProcessor
from core.permissions import IsStaffOrReadOnly

logger = logging.getLogger(__name__)


def _verify_signature(app_secret: str, raw_body: bytes, signature: str) -> bool:
    """Verify HMAC-SHA256 webhook signature."""
    computed = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)


@csrf_exempt
@require_POST
def tiktok_webhook(request: HttpRequest) -> JsonResponse:
    """
    Receive TikTok webhook events.
    URL: POST /tiktok/webhook/
    """
    raw_body = request.body

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid json"}, status=400)

    event_type = payload.get("type", "")
    shop_id = payload.get("shop_id", "")
    signature = request.headers.get("X-Tiktok-Signature", "")

    shop = None
    if shop_id:
        try:
            shop = TikTokShop.objects.get(shop_id=shop_id, is_active=True)
            if signature and not _verify_signature(shop.app_secret, raw_body, signature):
                logger.warning(f"Webhook signature mismatch for TikTok shop {shop_id}")
        except TikTokShop.DoesNotExist:
            logger.warning(f"Webhook received for unknown TikTok shop_id={shop_id}")

    log = TikTokWebhookLog.objects.create(
        shop=shop,
        event_type=event_type,
        payload=payload,
        processed=False,
    )

    processor = WebhookProcessor()
    processor.process(log)

    return JsonResponse({"message": "ok"})


class TikTokShopViewSet(viewsets.ModelViewSet):
    serializer_class = TikTokShopSerializer
    permission_classes = [IsStaffOrReadOnly]

    def get_queryset(self) -> QuerySet[TikTokShop]:
        user = self.request.user
        if user.is_authenticated:
            profile = getattr(user, "profile", None)
            if profile:
                return TikTokShop.objects.filter(company=profile.company)
        return TikTokShop.objects.all()

    @action(detail=True, methods=["post"], url_path="refresh-token")
    def refresh_token(self, request: Request, pk: str | None = None) -> Response:
        shop = self.get_object()
        from apps.marketplace.tiktok.client import TikTokAPIError, TikTokClient

        try:
            client = TikTokClient(shop)
            client.refresh_access_token()  # type: ignore[no-untyped-call]
            return Response({"message": "Token refreshed"})
        except TikTokAPIError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class TikTokWebhookLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TikTokWebhookLogSerializer
    permission_classes = [IsStaffOrReadOnly]

    def get_queryset(self) -> QuerySet[TikTokWebhookLog]:
        user = self.request.user
        if user.is_authenticated:
            profile = getattr(user, "profile", None)
            if profile:
                qs = TikTokWebhookLog.objects.filter(shop__company=profile.company).order_by(
                    "-cdate"
                )
            else:
                qs = TikTokWebhookLog.objects.all().order_by("-cdate")
        else:
            qs = TikTokWebhookLog.objects.all().order_by("-cdate")
        shop_id = self.request.query_params.get("shop_id")
        processed = self.request.query_params.get("processed")
        if shop_id:
            qs = qs.filter(shop__shop_id=shop_id)
        if processed is not None:
            qs = qs.filter(processed=processed.lower() == "true")
        return qs
