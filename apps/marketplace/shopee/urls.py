from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.marketplace.shopee import views

router = DefaultRouter()
router.register(r"shopee/shops", views.ShopeeShopViewSet, basename="shopee-shop")
router.register(
    r"shopee/webhook-logs", views.ShopeeWebhookLogViewSet, basename="shopee-webhook-log"
)
router.register(r"shopee/sync-logs", views.ShopeeSyncLogViewSet, basename="shopee-sync-log")

urlpatterns = [
    path("shopee/webhook/", views.shopee_webhook, name="shopee-webhook"),
    path("", include(router.urls)),
]
