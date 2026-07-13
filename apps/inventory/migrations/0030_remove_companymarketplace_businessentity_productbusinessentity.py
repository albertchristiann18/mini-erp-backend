from django.db import migrations


class Migration(migrations.Migration):
    """State-only migration: remove CompanyMarketplace, BusinessEntity, and ProductBusinessEntity
    from inventory app state. Database tables (inventory_companymarketplace,
    inventory_businessentity, inventory_productbusinessentity) and every FK constraint against
    them are left physically untouched (now owned by apps.marketplace).

    MUST depend on marketplace/0001_initial so that on a from-scratch replay the DeleteModel
    never runs before marketplace has adopted the models — the exact BE2 ordering-bug class.
    Delete in reverse-dependency order: ProductBusinessEntity before BusinessEntity before
    CompanyMarketplace."""

    dependencies = [
        ("inventory", "0029_remove_supplier_productsupplier"),
        ("marketplace", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="ProductBusinessEntity"),
                migrations.DeleteModel(name="BusinessEntity"),
                migrations.DeleteModel(name="CompanyMarketplace"),
            ],
            database_operations=[],
        ),
    ]
