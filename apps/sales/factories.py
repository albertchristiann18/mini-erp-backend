import factory
from django.utils import timezone

from apps.catalog.factories import ProductVariantFactory
from apps.sales.models import (
    SalesOrder,
    SalesOrderItem,
    SalesReturn,
    SalesReturnItem,
)
from core.factories import CompanyFactory, WarehouseFactory


class SalesOrderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SalesOrder

    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]
    warehouse = factory.SubFactory(WarehouseFactory)  # type: ignore[no-untyped-call]
    order_date = factory.LazyFunction(lambda: timezone.now())  # type: ignore[no-untyped-call]
    status = SalesOrder.OrderStatus.PENDING
    order_number = factory.Sequence(lambda n: f"SO-TEST-{n:04d}")  # type: ignore[no-untyped-call]


class SalesOrderItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SalesOrderItem

    company = factory.LazyAttribute(lambda o: o.sales_order.company)  # type: ignore[no-untyped-call]
    sales_order = factory.SubFactory(SalesOrderFactory)  # type: ignore[no-untyped-call]
    product_variant = factory.SubFactory(ProductVariantFactory)  # type: ignore[no-untyped-call]
    quantity = 1
    selling_price = 100000
    line_total = factory.LazyAttribute(lambda o: o.selling_price * o.quantity)  # type: ignore[no-untyped-call]


class SalesReturnFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SalesReturn

    company = factory.LazyAttribute(lambda o: o.sales_order.company)  # type: ignore[no-untyped-call]
    sales_order = factory.SubFactory(SalesOrderFactory)  # type: ignore[no-untyped-call]
    status = SalesReturn.ReturnStatus.REQUESTED
    return_number = factory.Sequence(lambda n: f"RET-TEST-{n:04d}")  # type: ignore[no-untyped-call]


class SalesReturnItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SalesReturnItem

    company = factory.LazyAttribute(lambda o: o.sales_return.company)  # type: ignore[no-untyped-call]
    sales_return = factory.SubFactory(SalesReturnFactory)  # type: ignore[no-untyped-call]
    sales_order_item = factory.SubFactory(SalesOrderItemFactory)  # type: ignore[no-untyped-call]
    product_variant = factory.LazyAttribute(lambda o: o.sales_order_item.product_variant)  # type: ignore[no-untyped-call]
    quantity = 1
