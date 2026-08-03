from rest_framework.routers import DefaultRouter

from apps.catalog import views

router = DefaultRouter()
router.register(r"category", views.CategoryViewSet, basename="category")
router.register(r"product", views.ProductViewSet)
router.register(
    r"product-variants", views.ProductVariantStockViewSet, basename="product-variant-stock"
)
router.register(r"master-categories", views.MasterCategoryViewSet, basename="master-category")

urlpatterns = router.urls
