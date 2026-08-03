from rest_framework.routers import DefaultRouter

from apps.marketplace import views

router = DefaultRouter()
router.register(r"marketplace", views.MarketplaceViewSet)
router.register(
    r"marketplace-connections",
    views.MarketplaceConnectionViewSet,
    basename="marketplace-connection",
)
router.register(
    r"company-marketplaces",
    views.CompanyMarketplaceViewSet,
    basename="company-marketplace",
)
router.register(r"business-entities", views.BusinessEntityViewSet, basename="business-entity")
router.register(
    r"product-business-entities",
    views.ProductBusinessEntityViewSet,
    basename="product-business-entity",
)

urlpatterns = router.urls
