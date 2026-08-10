from decimal import Decimal

from django.db import connection
from django.test import TestCase

from apps.purchasing.tests.factories import PurchaseOrderDetailFactory, PurchaseOrderFactory


class MigrationGraphOrderingRegressionTests(TestCase):
    """Regression tests proving the BE3 supplier state-move migrations are correctly
    ordered: the inventory DeleteModel migration must always run after the purchasing
    CreateModel/AlterField migration — on fresh replay and on rollback — preventing
    the BE2 ordering-bug class from recurring here."""

    def test_inventory_delete_supplier_runs_after_purchasing_create_supplier(self):
        """inventory/0029 (DeleteModel Supplier/ProductSupplier) must run after
        purchasing/0026 (CreateModel Supplier/ProductSupplier + AlterField) in every
        forwards plan that includes the inventory deletion migration."""
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)
        # Find the new inventory migration that deletes Supplier/ProductSupplier
        inventory_delete_node = ("inventory", "0029_remove_supplier_productsupplier")
        purchasing_create_node = ("purchasing", "0026_add_supplier_productsupplier")
        plan = loader.graph.forwards_plan(inventory_delete_node)
        self.assertIn(
            purchasing_create_node,
            plan,
            f"{purchasing_create_node} must run before {inventory_delete_node} or a "
            "from-scratch replay can delete the models before purchasing recreates them",
        )

    def test_pre_delete_project_state_renders_without_lazy_reference_errors(self):
        """Build the project state immediately BEFORE the inventory DeleteModel migration
        runs and force-render it — reproduces the exact crash class from BE2 where a
        backward executor force-renders state and hits a dangling lazy FK reference."""
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)
        inventory_delete_node = ("inventory", "0029_remove_supplier_productsupplier")
        state = loader.graph.make_state(
            nodes=[inventory_delete_node],
            at_end=False,
            real_apps=loader.unmigrated_apps,
        )
        try:
            state.apps
        except ValueError as exc:
            self.fail(
                "Rendering project state immediately before "
                f"{inventory_delete_node} crashed with a lazy-reference error — "
                "the purchasing CreateModel/AlterField migration is missing a "
                f"dependency edge: {exc}"
            )


class MoneyFieldsDecimalConversionRegressionTests(TestCase):
    """MONEY-4: purchasing/0027_alter_purchaseorder_commission_fee_and_more widens 14
    IDR money fields (PurchaseOrder.forecast_shipping_fee, forecast_shipping_fee_per_cbm,
    commission_fee, shipping_fee_per_cbm, shipping_fee, procure_amount, refund_amount,
    total_item_amount, total_order_amount, total_amount; PurchaseOrderDetail.
    unit_price_base, total_price_base, discounted_unit_price_base,
    discounted_total_price_base) from BigIntegerField to DecimalField(18, 2).

    Postgres's `ALTER COLUMN TYPE numeric` from bigint is an exact, lossless widening
    conversion — an existing integer value becomes the same value with a zero
    fractional part. These tests prove that end-to-end against the fully-migrated
    schema: a plain integer written through the ORM reads back, after a DB round
    trip, as the identical Decimal value."""

    def test_purchase_order_int_money_values_round_trip_as_identical_decimal(self):
        po = PurchaseOrderFactory(
            forecast_shipping_fee=120000,
            forecast_shipping_fee_per_cbm=2000000,
            commission_fee=50000,
            shipping_fee_per_cbm=1500000,
            shipping_fee=750000,
            procure_amount=800000,
            refund_amount=10000,
            total_item_amount=440000,
            total_order_amount=490000,
            total_amount=1240000,
        )
        po.refresh_from_db()

        self.assertIsInstance(po.forecast_shipping_fee, Decimal)
        self.assertEqual(po.forecast_shipping_fee, Decimal("120000.00"))
        self.assertIsInstance(po.forecast_shipping_fee_per_cbm, Decimal)
        self.assertEqual(po.forecast_shipping_fee_per_cbm, Decimal("2000000.00"))
        self.assertIsInstance(po.commission_fee, Decimal)
        self.assertEqual(po.commission_fee, Decimal("50000.00"))
        self.assertIsInstance(po.shipping_fee_per_cbm, Decimal)
        self.assertEqual(po.shipping_fee_per_cbm, Decimal("1500000.00"))
        self.assertIsInstance(po.shipping_fee, Decimal)
        self.assertEqual(po.shipping_fee, Decimal("750000.00"))
        self.assertIsInstance(po.procure_amount, Decimal)
        self.assertEqual(po.procure_amount, Decimal("800000.00"))
        self.assertIsInstance(po.refund_amount, Decimal)
        self.assertEqual(po.refund_amount, Decimal("10000.00"))
        self.assertIsInstance(po.total_item_amount, Decimal)
        self.assertEqual(po.total_item_amount, Decimal("440000.00"))
        self.assertIsInstance(po.total_order_amount, Decimal)
        self.assertEqual(po.total_order_amount, Decimal("490000.00"))
        self.assertIsInstance(po.total_amount, Decimal)
        self.assertEqual(po.total_amount, Decimal("1240000.00"))

    def test_purchase_order_detail_int_money_values_round_trip_as_identical_decimal(self):
        detail = PurchaseOrderDetailFactory(
            unit_price_base=22000,
            total_price_base=220000,
            discounted_unit_price_base=20000,
            discounted_total_price_base=200000,
        )
        detail.refresh_from_db()

        self.assertIsInstance(detail.unit_price_base, Decimal)
        self.assertEqual(detail.unit_price_base, Decimal("22000.00"))
        self.assertIsInstance(detail.total_price_base, Decimal)
        self.assertEqual(detail.total_price_base, Decimal("220000.00"))
        self.assertIsInstance(detail.discounted_unit_price_base, Decimal)
        self.assertEqual(detail.discounted_unit_price_base, Decimal("20000.00"))
        self.assertIsInstance(detail.discounted_total_price_base, Decimal)
        self.assertEqual(detail.discounted_total_price_base, Decimal("200000.00"))

    def test_purchase_order_detail_discounted_price_null_preserved(self):
        detail = PurchaseOrderDetailFactory(
            unit_price_base=10000,
            discounted_unit_price_base=None,
            discounted_total_price_base=None,
        )
        detail.refresh_from_db()

        self.assertIsNone(detail.discounted_unit_price_base)
        self.assertIsNone(detail.discounted_total_price_base)
