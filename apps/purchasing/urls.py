from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.purchasing import views as purchasing_views

router = DefaultRouter()
router.register(r"purchase-order", purchasing_views.PurchaseOrderViewSet, basename="purchase-order")

urlpatterns = [
    path("replenishment/", purchasing_views.ReplenishmentView.as_view(), name="replenishment"),
]
urlpatterns += router.urls
