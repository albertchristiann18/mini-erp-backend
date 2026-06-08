from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.inventory import views

urlpatterns = [
    path("avg-sales/", views.AvgSalesView.as_view(), name="inventory-avg-sales"),
    path("inventory-summary/", views.InventorySummaryView.as_view(), name="inventory-summary"),
]

router = DefaultRouter()
router.register(r"category", views.CategoryViewSet)
router.register(r"product", views.ProductViewSet)
router.register(
    r"product-variants", views.ProductVariantStockViewSet, basename="product-variant-stock"
)
router.register(r"warehouse", views.WarehouseViewSet)
router.register(r"master-categories", views.MasterCategoryViewSet, basename="master-category")
router.register(r"inventory", views.InventoryBulkViewSet, basename="inventory-bulk")
router.register(r"stock-movements", views.StockMovementViewSet, basename="stock-movement")
router.register(r"suppliers", views.SupplierViewSet, basename="supplier")
router.register(r"variant-suppliers", views.ProductVariantSupplierViewSet, basename="variant-supplier")

urlpatterns += router.urls
