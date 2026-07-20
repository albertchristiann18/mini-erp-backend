import factory

from apps.marketplace.models import (
    BusinessEntity,
    CompanyMarketplace,
    Marketplace,
    MarketplaceConnection,
    ProductBusinessEntity,
)
from core.factories import CompanyFactory


class MarketplaceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Marketplace

    name = "Default Marketplace"


class MarketplaceConnectionFactory(factory.django.DjangoModelFactory):
    """Factory for creating test MarketplaceConnection instances"""

    class Meta:
        model = MarketplaceConnection

    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]
    platform = "SHOPEE"
    display_name = "Test Shopee Connection"
    is_active = True


class CompanyMarketplaceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CompanyMarketplace

    name = factory.Sequence(lambda n: f"Marketplace {n}")  # type: ignore[no-untyped-call]
    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]
    is_active = True


class BusinessEntityFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = BusinessEntity

    name = factory.Sequence(lambda n: f"Business Entity {n}")  # type: ignore[no-untyped-call]
    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]
    marketplace = factory.SubFactory(CompanyMarketplaceFactory)  # type: ignore[no-untyped-call]
    is_active = True


class ProductBusinessEntityFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ProductBusinessEntity

    product = factory.SubFactory("apps.catalog.tests.factories.ProductFactory")  # type: ignore[no-untyped-call]
    business_entity = factory.SubFactory(BusinessEntityFactory)  # type: ignore[no-untyped-call]
    company = factory.LazyAttribute(  # type: ignore[attr-defined,no-untyped-call]
        lambda o: o.product.company
    )
