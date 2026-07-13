from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.marketplace.tiktok import views

router = DefaultRouter()
router.register(r"tiktok/shops", views.TikTokShopViewSet, basename="tiktok-shop")
router.register(
    r"tiktok/webhook-logs", views.TikTokWebhookLogViewSet, basename="tiktok-webhook-log"
)

urlpatterns = [
    path("tiktok/webhook/", views.tiktok_webhook, name="tiktok-webhook"),
    path("", include(router.urls)),
]
