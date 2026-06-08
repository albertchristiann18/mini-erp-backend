from decimal import Decimal

import factory

from apps.inventory.models import (
    Category,
    Product,
    ProductCogs,
    ProductVariant,
    ProductVariantMarketplace,
    ProductVariantSupplier,
    ProductVariantWarehouse,
    StockMovement,
    Supplier,
)
from core.factories import CompanyFactory, MarketplaceFactory, WarehouseFactory


class ProductFactory(factory.django.DjangoModelFactory):
    """Factory for creating test Product instances"""

    class Meta:
        model = Product

    name = "Test Product"
    category = factory.SubFactory("apps.inventory.factories.CategoryFactory")  # type: ignore[no-untyped-call]
    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]


class ProductVariantFactory(factory.django.DjangoModelFactory):
    """Factory for creating test ProductVariant instances"""

    class Meta:
        model = ProductVariant

    name = "Test Product Variant"
    product = factory.SubFactory(ProductFactory)  # type: ignore[no-untyped-call]
    company = factory.LazyAttribute(lambda o: o.product.company if o.product else None)  # type: ignore[attr-defined,no-untyped-call]
    variant_values = {}


class CategoryFactory(factory.django.DjangoModelFactory):
    """Factory for creating test Category instances"""

    class Meta:
        model = Category

    name = factory.Sequence(lambda n: f"Test Category {n}")  # type: ignore[no-untyped-call]
    category_code = factory.Sequence(lambda n: f"CAT-{n:04d}")  # type: ignore[no-untyped-call]
    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]


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


class ProductVariantMarketplaceFactory(factory.django.DjangoModelFactory):
    """Factory for creating test ProductVariantMarketplace instances"""

    class Meta:
        model = ProductVariantMarketplace

    product_variant = factory.SubFactory(ProductVariantFactory)  # type: ignore[no-untyped-call]
    marketplace = factory.SubFactory(MarketplaceFactory)  # type: ignore[no-untyped-call]
    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]
    selling_price = 10000
    is_active = True
    shopee_item_id = None
    shopee_model_id = None


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


class SupplierFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Supplier

    name = factory.Sequence(lambda n: f"Test Supplier {n}")
    contact_name = "Test Contact"
    phone = "12345678"
    country = "China"
    is_active = True
    company = factory.SubFactory(CompanyFactory)


class ProductVariantSupplierFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ProductVariantSupplier

    product_variant = factory.SubFactory(ProductVariantFactory)
    supplier = factory.SubFactory(SupplierFactory)
    company = factory.LazyAttribute(lambda o: o.product_variant.company)
    supplier_link = None
    is_primary = False
    notes = None
