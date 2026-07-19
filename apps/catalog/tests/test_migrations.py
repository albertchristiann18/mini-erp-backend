from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

from apps.catalog.models import Category, Product, ProductVariant


class DatabaseSchemaRegressionTests(TestCase):
    """Tests to verify the master_* table renaming was successful at the schema level."""

    def test_table_names_updated_in_django_meta(self):
        """Verify that all 6 models have their db_table set to master_*."""
        # Catalog models
        self.assertEqual(Category._meta.db_table, "master_category")
        self.assertEqual(Product._meta.db_table, "master_product")
        from apps.catalog.models import ProductPhoto, ProductVariantMarketplace

        self.assertEqual(ProductPhoto._meta.db_table, "master_productphoto")
        self.assertEqual(ProductVariant._meta.db_table, "master_productvariant")
        self.assertEqual(
            ProductVariantMarketplace._meta.db_table, "master_productvariantmarketplace"
        )

        # Inventory model
        from apps.inventory.models import Warehouse

        self.assertEqual(Warehouse._meta.db_table, "master_warehouse")

    def test_old_tables_absent_new_tables_present_in_db(self):
        """Verify that physical tables in the database use the new master_* names."""
        expected_tables = [
            "master_category",
            "master_product",
            "master_productphoto",
            "master_productvariant",
            "master_productvariantmarketplace",
            "master_warehouse",
        ]
        old_tables = [
            "inventory_category",
            "inventory_product",
            "product_photo",
            "inventory_productvariant",
            "inventory_productvariantmarketplace",
            "inventory_warehouse",
        ]

        with connection.cursor() as cursor:
            # Check that all new tables exist
            for table_name in expected_tables:
                cursor.execute(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = %s AND table_schema = 'public')",
                    [table_name],
                )
                self.assertTrue(
                    cursor.fetchone()[0],
                    f"New table {table_name} not found in database",
                )

            # Check that all old tables are gone
            for table_name in old_tables:
                cursor.execute(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = %s AND table_schema = 'public')",
                    [table_name],
                )
                self.assertFalse(
                    cursor.fetchone()[0], f"Old table {table_name} still exists in database"
                )


class MigrationGraphOrderingRegressionTests(TestCase):
    """Regression tests proving the master_* rename migrations (catalog/0003,
    inventory/0027) cannot be scheduled before any migration that creates a real
    FK constraint against the pre-rename table names. Without these edges, Django's
    migration plan is under-constrained and a from-scratch replay can (non-
    deterministically) sequence the rename before a deferred FK-creation statement
    fires, producing "relation ... does not exist" — the bug fixed in this pass."""

    def test_catalog_master_table_rename_runs_after_all_fk_creating_migrations(self):
        loader = MigrationLoader(connection)
        plan = loader.graph.forwards_plan(("catalog", "0003_rename_master_tables"))
        for dependency in [
            ("sales", "0001_initial"),
            ("purchasing", "0001_initial"),
            ("shopee", "0002_add_shopee_stock_sync_log"),
        ]:
            self.assertIn(
                dependency,
                plan,
                f"{dependency} must run before catalog/0003_rename_master_tables or its "
                "deferred FK constraint SQL will reference an already-renamed table",
            )

    def test_inventory_warehouse_rename_runs_after_all_fk_creating_migrations(self):
        loader = MigrationLoader(connection)
        plan = loader.graph.forwards_plan(("inventory", "0027_rename_warehouse_table"))
        for dependency in [
            ("sales", "0001_initial"),
            ("purchasing", "0001_initial"),
            ("shopee", "0001_initial"),
            ("tiktok", "0001_initial"),
        ]:
            self.assertIn(
                dependency,
                plan,
                f"{dependency} must run before inventory/0027_rename_warehouse_table or its "
                "deferred FK constraint SQL will reference an already-renamed table",
            )

    def test_catalog_master_table_rename_runs_after_fk_retargeting_migrations(self):
        """catalog/0003 must run after the four 'noop_fk_refs' migrations that
        retarget ProductBusinessEntity/ProductCogs/ProductDimensionImage/
        ProductSupplier/ProductVariantWarehouse/StockMovement/PurchaseOrderDetail/
        SalesOrderItem/SalesReturnItem/ShopeeStockSyncLog's FK fields away from the
        inventory.product/inventory.productvariant models inventory/0025 deletes.
        Without this edge, a from-scratch topological replay can sequence
        catalog/0003 before one of these siblings, leaving a dangling lazy FK
        reference at the exact point Django force-renders state during a
        backward migration plan."""
        loader = MigrationLoader(connection)
        plan = loader.graph.forwards_plan(("catalog", "0003_rename_master_tables"))
        for dependency in [
            ("inventory", "0026_noop_fk_refs"),
            ("purchasing", "0025_noop_fk_refs"),
            ("sales", "0004_noop_fk_refs"),
            ("shopee", "0003_noop_fk_refs"),
        ]:
            self.assertIn(
                dependency,
                plan,
                f"{dependency} must run before catalog/0003_rename_master_tables or a "
                "backward migration plan can force-render project state while this "
                "sibling's FK retargeting hasn't happened yet, crashing with a lazy "
                "reference to a deleted inventory model",
            )

    def test_catalog_master_table_rename_predecessor_state_renders_without_lazy_reference_errors(
        self,
    ):
        """Reproduces the exact crash from the original bug: build the project
        state as it exists immediately BEFORE catalog/0003_rename_master_tables
        runs (the same computation Django's backward executor performs and
        force-validates at that point) and force a full render. Before the fix
        in this pass, this crashed with 'declared with a lazy reference to
        inventory.product, but app inventory doesn't provide model product' —
        this test exercises the actual backward-plan render logic, not just
        forwards_plan membership, so this class of bug is caught by CI."""
        loader = MigrationLoader(connection)
        state = loader.graph.make_state(
            nodes=[("catalog", "0003_rename_master_tables")],
            at_end=False,
            real_apps=loader.unmigrated_apps,
        )
        try:
            state.apps
        except ValueError as exc:
            self.fail(
                "Rendering project state immediately before "
                "catalog/0003_rename_master_tables crashed with a lazy-reference "
                "error — catalog/0003 is missing a dependency edge on one of the "
                "FK-retargeting 'noop_fk_refs' migrations (inventory/0026, "
                f"purchasing/0025, sales/0004, shopee/0003): {exc}"
            )

    def test_catalog_create_productdimensionimage_runs_before_inventory_delete(self):
        """catalog/0005_state_only_create_productdimensionimage must be ordered before
        inventory/0031_state_only_delete_productdimensionimage in the forward plan.
        Without this edge a from-scratch replay could delete the model from inventory
        state before catalog has adopted it, leaving a dangling lazy FK reference."""
        loader = MigrationLoader(connection)
        plan = loader.graph.forwards_plan(
            ("inventory", "0031_state_only_delete_productdimensionimage")
        )
        self.assertIn(
            ("catalog", "0005_state_only_create_productdimensionimage"),
            plan,
            "catalog/0005 must run before inventory/0031 or the DeleteModel runs "
            "before catalog has registered ProductDimensionImage, breaking lazy FK "
            "resolution on a from-scratch replay",
        )

    def test_inventory_delete_productdimensionimage_predecessor_state_renders_without_lazy_reference_errors(
        self,
    ):
        """Build the project state immediately BEFORE inventory/0031 runs and force a
        full render. This catches the BE2-class bug where a DeleteModel is scheduled
        before the adopting app's CreateModel, producing a lazy-reference crash at
        render time. The state at that point must have ProductDimensionImage owned by
        catalog (via catalog/0005) and still present in inventory (not yet deleted)."""
        loader = MigrationLoader(connection)
        state = loader.graph.make_state(
            nodes=[("inventory", "0031_state_only_delete_productdimensionimage")],
            at_end=False,
            real_apps=loader.unmigrated_apps,
        )
        try:
            state.apps
        except ValueError as exc:
            self.fail(
                "Rendering project state immediately before "
                "inventory/0031_state_only_delete_productdimensionimage crashed with a "
                "lazy-reference error — inventory/0031 is missing the dependency edge on "
                f"catalog/0005_state_only_create_productdimensionimage: {exc}"
            )

    def test_inventory_retarget_productvariant_fks_runs_after_catalog_productvariant_created(
        self,
    ):
        """inventory/0032 (AlterField x3 retargeting FKs to catalog.productvariant then
        DeleteModel of the proxy) must run AFTER catalog.ProductVariant is created so the
        to='catalog.productvariant' FK target resolves without error. Verify the full
        forward plan for inventory/0032 includes catalog/0005, the explicit pinned
        dependency edge that guarantees the catalog side is fully migrated (ProductVariant
        present in catalog state) before the inventory FK-retarget/proxy-DeleteModel runs."""
        loader = MigrationLoader(connection)
        plan = loader.graph.forwards_plan(
            ("inventory", "0032_state_only_retarget_productvariant_fks")
        )
        self.assertIn(
            ("catalog", "0005_state_only_create_productdimensionimage"),
            plan,
            "catalog/0005_state_only_create_productdimensionimage must run before "
            "inventory/0032 — this is the explicit pinned dependency edge that ensures "
            "the catalog side is fully migrated before the FK-retarget/proxy-DeleteModel "
            "runs",
        )

    def test_inventory_retarget_productvariant_fks_predecessor_state_renders_without_lazy_reference_errors(
        self,
    ):
        """Build the project state immediately BEFORE inventory/0032 runs and force a
        full render. Catches any lazy-reference error that would result from the proxy
        ProductVariant still being registered in inventory state at that point while
        the three FK fields still point at inventory.productvariant — the render must
        succeed because 0028 registered the proxy and the FKs are still valid before
        0032 removes them."""
        loader = MigrationLoader(connection)
        state = loader.graph.make_state(
            nodes=[("inventory", "0032_state_only_retarget_productvariant_fks")],
            at_end=False,
            real_apps=loader.unmigrated_apps,
        )
        try:
            state.apps
        except ValueError as exc:
            self.fail(
                "Rendering project state immediately before "
                "inventory/0032_state_only_retarget_productvariant_fks crashed with a "
                "lazy-reference error — the migration graph is missing a required "
                f"dependency edge: {exc}"
            )
