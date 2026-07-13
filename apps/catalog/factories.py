import factory

from apps.catalog.models import (
    Category,
    Product,
    ProductPhoto,
    ProductVariant,
    ProductVariantMarketplace,
)
from core.factories import CompanyFactory


class CategoryFactory(factory.django.DjangoModelFactory):
    """Factory for creating test Category instances"""

    class Meta:
        model = Category

    name = factory.Sequence(lambda n: f"Test Category {n}")  # type: ignore[no-untyped-call]
    category_code = factory.Sequence(lambda n: f"CAT-{n:04d}")  # type: ignore[no-untyped-call]
    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]


class ProductFactory(factory.django.DjangoModelFactory):
    """Factory for creating test Product instances"""

    class Meta:
        model = Product

    name = "Test Product"
    category = factory.SubFactory("apps.catalog.factories.CategoryFactory")  # type: ignore[no-untyped-call]
    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]


class ProductVariantFactory(factory.django.DjangoModelFactory):
    """Factory for creating test ProductVariant instances"""

    class Meta:
        model = ProductVariant

    name = "Test Product Variant"
    product = factory.SubFactory(ProductFactory)  # type: ignore[no-untyped-call]
    company = factory.LazyAttribute(lambda o: o.product.company if o.product else None)  # type: ignore[attr-defined,no-untyped-call]
    variant_values = {}
    sku_variant_code = factory.Sequence(lambda n: f"SKU-VAR-{n:04d}")  # type: ignore[no-untyped-call]


class ProductPhotoFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ProductPhoto

    product = factory.SubFactory(ProductFactory)  # type: ignore[no-untyped-call]
    company = factory.LazyAttribute(lambda o: o.product.company if o.product else None)  # type: ignore[attr-defined,no-untyped-call]
    image = factory.django.FileField(data=b"x", filename="test_photo.jpg")  # type: ignore[no-untyped-call]
    order = 0
    is_primary = False


class ProductVariantMarketplaceFactory(factory.django.DjangoModelFactory):
    """Factory for creating test ProductVariantMarketplace instances"""

    class Meta:
        model = ProductVariantMarketplace

    product_variant = factory.SubFactory(ProductVariantFactory)  # type: ignore[no-untyped-call]
    marketplace = factory.SubFactory("apps.marketplace.factories.MarketplaceFactory")  # type: ignore[no-untyped-call]
    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]
    selling_price = 10000
    is_active = True
