from django.db import connection
from django.test import TestCase


class MigrationGraphOrderingRegressionTests(TestCase):
    """Regression tests proving the BE4b marketplace state-move migrations are correctly
    ordered: marketplace/0001_initial must always run before core/0003 (DeleteModel),
    inventory/0030 (DeleteModel), and the three noop_fk_refs retarget migrations —
    on fresh replay and on rollback — preventing the BE2 ordering-bug class from
    recurring here."""

    def test_marketplace_initial_runs_before_core_delete_marketplace(self):
        """core/0003 (DeleteModel Marketplace/MarketplaceConnection) must run after
        marketplace/0001_initial (CreateModel x5) in every forwards plan."""
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)
        core_delete_node = ("core", "0003_remove_marketplace_marketplaceconnection")
        marketplace_create_node = ("marketplace", "0001_initial")
        plan = loader.graph.forwards_plan(core_delete_node)
        self.assertIn(
            marketplace_create_node,
            plan,
            f"{marketplace_create_node} must run before {core_delete_node} or a "
            "from-scratch replay can delete the models before marketplace adopts them",
        )

    def test_marketplace_initial_runs_before_inventory_delete_models(self):
        """inventory/0030 (DeleteModel x3) must run after marketplace/0001_initial."""
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)
        inventory_delete_node = (
            "inventory",
            "0030_remove_companymarketplace_businessentity_productbusinessentity",
        )
        marketplace_create_node = ("marketplace", "0001_initial")
        plan = loader.graph.forwards_plan(inventory_delete_node)
        self.assertIn(
            marketplace_create_node,
            plan,
            f"{marketplace_create_node} must run before {inventory_delete_node} or a "
            "from-scratch replay can delete the models before marketplace adopts them",
        )

    def test_marketplace_initial_runs_before_sales_noop_fk_refs(self):
        """sales/0006_noop_fk_refs must run after marketplace/0001_initial."""
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)
        sales_retarget_node = ("sales", "0006_noop_fk_refs")
        marketplace_create_node = ("marketplace", "0001_initial")
        plan = loader.graph.forwards_plan(sales_retarget_node)
        self.assertIn(
            marketplace_create_node,
            plan,
            f"{marketplace_create_node} must run before {sales_retarget_node}",
        )

    def test_marketplace_initial_runs_before_catalog_noop_fk_refs(self):
        """catalog/0004_noop_fk_refs must run after marketplace/0001_initial."""
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)
        catalog_retarget_node = ("catalog", "0004_noop_fk_refs")
        marketplace_create_node = ("marketplace", "0001_initial")
        plan = loader.graph.forwards_plan(catalog_retarget_node)
        self.assertIn(
            marketplace_create_node,
            plan,
            f"{marketplace_create_node} must run before {catalog_retarget_node}",
        )

    def test_marketplace_initial_runs_before_shopee_noop_fk_refs(self):
        """shopee/0004_noop_fk_refs must run after marketplace/0001_initial."""
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)
        shopee_retarget_node = ("shopee", "0004_noop_fk_refs")
        marketplace_create_node = ("marketplace", "0001_initial")
        plan = loader.graph.forwards_plan(shopee_retarget_node)
        self.assertIn(
            marketplace_create_node,
            plan,
            f"{marketplace_create_node} must run before {shopee_retarget_node}",
        )

    def test_pre_delete_core_project_state_renders_without_lazy_reference_errors(self):
        """Build the project state immediately BEFORE core/0003 (DeleteModel) runs
        and force-render it — reproduces the exact crash class from BE2."""
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)
        core_delete_node = ("core", "0003_remove_marketplace_marketplaceconnection")
        state = loader.graph.make_state(
            nodes=[core_delete_node],
            at_end=False,
            real_apps=loader.unmigrated_apps,
        )
        try:
            state.apps
        except ValueError as exc:
            self.fail(
                "Rendering project state immediately before "
                f"{core_delete_node} crashed with a lazy-reference error — "
                f"missing a dependency edge: {exc}"
            )

    def test_pre_delete_inventory_project_state_renders_without_lazy_reference_errors(self):
        """Build the project state immediately BEFORE inventory/0030 (DeleteModel) runs
        and force-render it — reproduces the exact crash class from BE2."""
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)
        inventory_delete_node = (
            "inventory",
            "0030_remove_companymarketplace_businessentity_productbusinessentity",
        )
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
                f"missing a dependency edge: {exc}"
            )
