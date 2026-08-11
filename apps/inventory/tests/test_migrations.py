from decimal import Decimal

from django.test import TestCase

from apps.inventory.tests.factories import ProductCogsFactory


class MoneyFieldsDecimalConversionRegressionTests(TestCase):
    """MONEY-5: inventory/0033_alter_productcogs_allocated_commission_fee_and_more widens
    4 IDR money fields on the FIFO layer (ProductCogs.cogs_amount,
    allocated_shipping_fee, allocated_delivery_fee, allocated_commission_fee) from
    BigIntegerField to DecimalField(18, 2). price_rmb (already Decimal) is untouched.
    exchange_rate was deliberately deferred at the time (BigIntegerField) and has since
    been converted separately by MONEY-8 (inventory/0034) — see
    ExchangeRateDecimalConversionRegressionTests below.

    Postgres's `ALTER COLUMN TYPE numeric` from bigint is an exact, lossless widening
    conversion — an existing integer value becomes the same value with a zero
    fractional part. These tests prove that end-to-end against the fully-migrated
    schema: a plain integer written through the ORM reads back, after a DB round
    trip, as the identical Decimal value."""

    def test_product_cogs_int_money_values_round_trip_as_identical_decimal(self):
        cogs = ProductCogsFactory(
            cogs_amount=22000,
            allocated_shipping_fee=1000,
            allocated_delivery_fee=221100,
            allocated_commission_fee=22000,
        )
        cogs.refresh_from_db()

        self.assertIsInstance(cogs.cogs_amount, Decimal)
        self.assertEqual(cogs.cogs_amount, Decimal("22000.00"))
        self.assertIsInstance(cogs.allocated_shipping_fee, Decimal)
        self.assertEqual(cogs.allocated_shipping_fee, Decimal("1000.00"))
        self.assertIsInstance(cogs.allocated_delivery_fee, Decimal)
        self.assertEqual(cogs.allocated_delivery_fee, Decimal("221100.00"))
        self.assertIsInstance(cogs.allocated_commission_fee, Decimal)
        self.assertEqual(cogs.allocated_commission_fee, Decimal("22000.00"))

    def test_product_cogs_price_rmb_untouched(self):
        cogs = ProductCogsFactory(price_rmb=Decimal("10.0000"))
        cogs.refresh_from_db()

        self.assertIsInstance(cogs.price_rmb, Decimal)
        self.assertEqual(cogs.price_rmb, Decimal("10.0000"))


class ExchangeRateDecimalConversionRegressionTests(TestCase):
    """MONEY-8: inventory/0034_productcogs_exchange_rate_decimal converts
    ProductCogs.exchange_rate from BigIntegerField to DecimalField(10, 3), matching
    PurchaseOrder.exchange_rate precision. As with the MONEY-5 IDR money-field
    widening, Postgres's `ALTER COLUMN TYPE numeric` from bigint is an exact,
    lossless conversion for any value the bigint column could already hold — a
    pre-existing whole-number rate becomes the same value with a zero fractional
    part. This test proves that end-to-end against the fully-migrated schema: a
    whole-number rate written through the ORM reads back, after a DB round trip,
    as the identical Decimal value."""

    def test_product_cogs_whole_number_exchange_rate_round_trips_as_identical_decimal(self):
        cogs = ProductCogsFactory(exchange_rate=Decimal("2200"))
        cogs.refresh_from_db()

        self.assertIsInstance(cogs.exchange_rate, Decimal)
        self.assertEqual(cogs.exchange_rate, Decimal("2200.000"))
