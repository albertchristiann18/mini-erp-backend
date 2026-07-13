from decimal import Decimal

import factory

from apps.catalog.factories import ProductFactory, ProductVariantFactory
from apps.inventory.models import (
    ProductCogs,
    ProductDimensionImage,
    ProductVariantWarehouse,
    StockMovement,
)
from core.factories import CompanyFactory, WarehouseFactory


class ProductCogsFactory(factory.django.DjangoModelFactory):
    """Factory for creating test ProductCogs instances"""

    class Meta:
        model = ProductCogs

    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]
    product_variant = factory.SubFactory(ProductVariantFactory)  # type: ignore[no-untyped-call]
    warehouse = factory.SubFactory(WarehouseFactory)  # type: ignore[no-untyped-call]
    reference_number = "PO-TEST-001"
    purchase_date = "2026-03-01"
    price_rmb = Decimal("1000.0000")
    exchange_rate = 2200
    cogs_amount = factory.LazyAttribute(lambda o: int(o.price_rmb * o.exchange_rate))  # type: ignore[no-untyped-call]
    original_qty = 50
    remaining_qty = 50


class ProductVariantWarehouseFactory(factory.django.DjangoModelFactory):
    """Factory for creating test ProductVariantWarehouse instances"""

    class Meta:
        model = ProductVariantWarehouse

    product_variant = factory.SubFactory(ProductVariantFactory)  # type: ignore[no-untyped-call]
    warehouse = factory.SubFactory(WarehouseFactory)  # type: ignore[no-untyped-call]
    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]
    incoming_qty = 0
    outgoing_qty = 0
    physical_qty = 0
    checkout_qty = 0


class StockMovementFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = StockMovement

    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]
    product_variant = factory.SubFactory(ProductVariantFactory)  # type: ignore[no-untyped-call]
    warehouse = factory.SubFactory(WarehouseFactory)  # type: ignore[no-untyped-call]
    movement_type = StockMovement.MovementType.INBOUND
    field_change = ""
    quantity = 10
    balance_before = 0
    balance_after = 10
    reference_number = ""
    note = ""


class ProductDimensionImageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ProductDimensionImage

    product = factory.SubFactory(ProductFactory)  # type: ignore[no-untyped-call]
    company = factory.LazyAttribute(lambda o: o.product.company if o.product else None)  # type: ignore[attr-defined,no-untyped-call]
    dim_key = "Warna"
    dim_value = "White"
    photo = factory.django.FileField(data=b"x", filename="test_dim_image.jpg")  # type: ignore[no-untyped-call]
