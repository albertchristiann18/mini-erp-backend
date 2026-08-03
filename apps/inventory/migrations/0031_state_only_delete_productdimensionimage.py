from django.db import migrations


class Migration(migrations.Migration):
    """State-only migration: remove ProductDimensionImage from inventory app state.
    The physical table (inventory_productdimensionimage) and every FK constraint against it
    are left physically untouched (now owned by apps.catalog).

    MUST depend on catalog/0005_state_only_create_productdimensionimage so that on a
    from-scratch replay the DeleteModel never runs before catalog has adopted the model —
    the exact BE2 ordering-bug class."""

    dependencies = [
        ("inventory", "0030_remove_companymarketplace_businessentity_productbusinessentity"),
        ("catalog", "0005_state_only_create_productdimensionimage"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="ProductDimensionImage"),
            ],
            database_operations=[],
        ),
    ]
