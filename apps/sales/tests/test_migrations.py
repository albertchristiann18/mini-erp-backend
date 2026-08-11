from decimal import Decimal

from django.test import TestCase

from apps.sales.tests.factories import (
    SalesOrderCogsDetailFactory,
    SalesOrderFactory,
    SalesOrderItemFactory,
    SalesReturnFactory,
    SalesReturnItemFactory,
)


class MoneyFieldsDecimalConversionRegressionTests(TestCase):
    """MONEY-6: sales/0007_alter_salesorder_gross_profit_and_more widens 20 IDR money
    fields across SalesOrder, SalesOrderItem, SalesOrderCogsDetail, SalesReturn and
    SalesReturnItem from BigIntegerField to DecimalField(18, 2).

    Postgres's `ALTER COLUMN TYPE numeric` from bigint is an exact, lossless widening
    conversion — an existing integer value becomes the same value with a zero
    fractional part. These tests prove that end-to-end against the fully-migrated
    schema: a plain integer written through the ORM reads back, after a DB round
    trip, as the identical Decimal value."""

    def test_sales_order_int_money_values_round_trip_as_identical_decimal(self):
        so = SalesOrderFactory(
            shipping_fee=10000,
            shipping_fee_seller=9000,
            subtotal=200000,
            total_discount=15000,
            total_marketplace_fee=8000,
            total_cogs=100000,
            net_revenue=177000,
            gross_profit=77000,
        )
        so.refresh_from_db()

        self.assertIsInstance(so.shipping_fee, Decimal)
        self.assertEqual(so.shipping_fee, Decimal("10000.00"))
        self.assertIsInstance(so.shipping_fee_seller, Decimal)
        self.assertEqual(so.shipping_fee_seller, Decimal("9000.00"))
        self.assertIsInstance(so.subtotal, Decimal)
        self.assertEqual(so.subtotal, Decimal("200000.00"))
        self.assertIsInstance(so.total_discount, Decimal)
        self.assertEqual(so.total_discount, Decimal("15000.00"))
        self.assertIsInstance(so.total_marketplace_fee, Decimal)
        self.assertEqual(so.total_marketplace_fee, Decimal("8000.00"))
        self.assertIsInstance(so.total_cogs, Decimal)
        self.assertEqual(so.total_cogs, Decimal("100000.00"))
        self.assertIsInstance(so.net_revenue, Decimal)
        self.assertEqual(so.net_revenue, Decimal("177000.00"))
        self.assertIsInstance(so.gross_profit, Decimal)
        self.assertEqual(so.gross_profit, Decimal("77000.00"))

    def test_sales_order_item_int_money_values_round_trip_as_identical_decimal(self):
        item = SalesOrderItemFactory(
            selling_price=100000,
            discount_amount=5000,
            commission_fee=3000,
            service_fee=2000,
            total_marketplace_fee=5000,
            actual_cogs_per_unit=50000,
            actual_cogs_total=50000,
            line_total=95000,
        )
        item.refresh_from_db()

        self.assertIsInstance(item.selling_price, Decimal)
        self.assertEqual(item.selling_price, Decimal("100000.00"))
        self.assertIsInstance(item.discount_amount, Decimal)
        self.assertEqual(item.discount_amount, Decimal("5000.00"))
        self.assertIsInstance(item.commission_fee, Decimal)
        self.assertEqual(item.commission_fee, Decimal("3000.00"))
        self.assertIsInstance(item.service_fee, Decimal)
        self.assertEqual(item.service_fee, Decimal("2000.00"))
        self.assertIsInstance(item.total_marketplace_fee, Decimal)
        self.assertEqual(item.total_marketplace_fee, Decimal("5000.00"))
        self.assertIsInstance(item.actual_cogs_per_unit, Decimal)
        self.assertEqual(item.actual_cogs_per_unit, Decimal("50000.00"))
        self.assertIsInstance(item.actual_cogs_total, Decimal)
        self.assertEqual(item.actual_cogs_total, Decimal("50000.00"))
        self.assertIsInstance(item.line_total, Decimal)
        self.assertEqual(item.line_total, Decimal("95000.00"))

    def test_sales_order_cogs_detail_int_money_values_round_trip_as_identical_decimal(self):
        detail = SalesOrderCogsDetailFactory(
            quantity_consumed=5,
            cogs_per_unit=50000,
            total_cogs=250000,
        )
        detail.refresh_from_db()

        self.assertIsInstance(detail.cogs_per_unit, Decimal)
        self.assertEqual(detail.cogs_per_unit, Decimal("50000.00"))
        self.assertIsInstance(detail.total_cogs, Decimal)
        self.assertEqual(detail.total_cogs, Decimal("250000.00"))

    def test_sales_return_int_money_values_round_trip_as_identical_decimal(self):
        sales_return = SalesReturnFactory(refund_amount=45000)
        sales_return.refresh_from_db()

        self.assertIsInstance(sales_return.refund_amount, Decimal)
        self.assertEqual(sales_return.refund_amount, Decimal("45000.00"))

    def test_sales_return_item_int_money_values_round_trip_as_identical_decimal(self):
        return_item = SalesReturnItemFactory(reversed_cogs_total=15000)
        return_item.refresh_from_db()

        self.assertIsInstance(return_item.reversed_cogs_total, Decimal)
        self.assertEqual(return_item.reversed_cogs_total, Decimal("15000.00"))
