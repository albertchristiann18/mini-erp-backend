from decimal import Decimal

import factory

from apps.inventory.factories import (
    CategoryFactory,
    CompanyFactory,
    ProductVariantFactory,
    SupplierFactory,
    WarehouseFactory,
)
from apps.purchasing.models import (
    PurchaseOrder,
    PurchaseOrderDetail,
    SourcingPool,
    SourcingPoolItem,
)


class PurchaseOrderFactory(factory.django.DjangoModelFactory):
    """Factory for creating test PurchaseOrder instances"""

    class Meta:
        model = PurchaseOrder

    purchase_order_number = factory.Sequence(lambda n: f"PO-{n:04d}")  # type: ignore[no-untyped-call]
    warehouse = factory.SubFactory(WarehouseFactory)  # type: ignore[no-untyped-call]
    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]
    supplier_name = "Test Supplier"
    status = "DRAFT"
    total_ordered_qty = 100
    total_amount = 1000000


class PurchaseOrderDetailFactory(factory.django.DjangoModelFactory):
    """Factory for creating test PurchaseOrderDetail instances"""

    class Meta:
        model = PurchaseOrderDetail

    purchase_order = factory.SubFactory(PurchaseOrderFactory)  # type: ignore[no-untyped-call]
    product_variant = factory.SubFactory(ProductVariantFactory)  # type: ignore[no-untyped-call]
    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]
    ordered_qty = 50
    unit_price_base = 10000
    total_price_base = 500000


class SourcingPoolFactory(factory.django.DjangoModelFactory):
    """Factory for creating test SourcingPool instances"""

    class Meta:
        model = SourcingPool

    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]
    supplier = factory.SubFactory(SupplierFactory)  # type: ignore[no-untyped-call]


class SourcingPoolItemFactory(factory.django.DjangoModelFactory):
    """Factory for creating test SourcingPoolItem instances"""

    class Meta:
        model = SourcingPoolItem

    pool = factory.SubFactory(SourcingPoolFactory)  # type: ignore[no-untyped-call]
    company = factory.SelfAttribute("pool.company")  # type: ignore[no-untyped-call]
    product_name = factory.Sequence(lambda n: f"Product-{n}")  # type: ignore[no-untyped-call]
    variant_name = "Default"
    category = factory.SubFactory(CategoryFactory)  # type: ignore[no-untyped-call]
    unit_price = Decimal("10.000")
    supplier_link = ""
