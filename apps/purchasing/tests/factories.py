import factory

from apps.catalog.tests.factories import ProductFactory, ProductVariantFactory
from apps.purchasing.models import (
    ProductSupplier,
    PurchaseOrder,
    PurchaseOrderDetail,
    Supplier,
)
from core.factories import CompanyFactory, WarehouseFactory


class SupplierFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Supplier

    name = factory.Sequence(lambda n: f"Test Supplier {n}")  # type: ignore[no-untyped-call]
    contact_name = "Test Contact"
    phone = "12345678"
    country = "China"
    is_active = True
    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]


class ProductSupplierFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ProductSupplier

    product = factory.SubFactory(ProductFactory)  # type: ignore[no-untyped-call]
    supplier = factory.SubFactory(SupplierFactory)  # type: ignore[no-untyped-call]
    company = factory.LazyAttribute(  # type: ignore[attr-defined,no-untyped-call]
        lambda o: o.product.company
    )
    supplier_link = None


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
