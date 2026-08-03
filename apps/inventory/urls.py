from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.inventory import views

urlpatterns = [
    path("avg-sales/", views.AvgSalesView.as_view(), name="inventory-avg-sales"),
    path("inventory-summary/", views.InventorySummaryView.as_view(), name="inventory-summary"),
]

router = DefaultRouter()
router.register(r"warehouse", views.WarehouseViewSet)
router.register(r"inventory", views.InventoryBulkViewSet, basename="inventory-bulk")
router.register(r"stock-movements", views.StockMovementViewSet, basename="stock-movement")

urlpatterns += router.urls
