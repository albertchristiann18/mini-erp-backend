from django.db import connection
from django.test import TestCase


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
