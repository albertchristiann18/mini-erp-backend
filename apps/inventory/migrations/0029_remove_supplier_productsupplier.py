from django.db import migrations


class Migration(migrations.Migration):
    """State-only migration: remove Supplier and ProductSupplier from inventory app
    state. Database tables (inventory_supplier, inventory_productsupplier) and every FK
    constraint against them are left physically untouched (now owned by apps.purchasing).

    MUST depend on purchasing/0026_add_supplier_productsupplier so that on a from-scratch
    replay the DeleteModel never runs before purchasing has recreated the models and
    retargeted PurchaseOrder.supplier — the exact BE2 ordering-bug class."""

    dependencies = [
        ("inventory", "0028_productvariant_alter_productcogs_product_variant_and_more"),
        ("purchasing", "0026_add_supplier_productsupplier"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="ProductSupplier"),
                migrations.DeleteModel(name="Supplier"),
            ],
            database_operations=[],
        ),
    ]
