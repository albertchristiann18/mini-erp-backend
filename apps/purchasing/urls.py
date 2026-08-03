from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.purchasing import views as purchasing_views

router = DefaultRouter()
router.register(r"purchase-order", purchasing_views.PurchaseOrderViewSet, basename="purchase-order")
router.register(r"sourcing-pool", purchasing_views.SourcingPoolViewSet, basename="sourcing-pool")
router.register(r"suppliers", purchasing_views.SupplierViewSet, basename="supplier")
router.register(
    r"product-suppliers", purchasing_views.ProductSupplierViewSet, basename="product-supplier"
)

urlpatterns = [
    path("replenishment/", purchasing_views.ReplenishmentView.as_view(), name="replenishment"),
]
urlpatterns += router.urls
