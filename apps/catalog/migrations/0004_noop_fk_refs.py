import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """State-only migration: retarget ProductVariantMarketplace.marketplace FK from
    core.Marketplace to marketplace.Marketplace. The DB column and FK constraint are
    physically unchanged because the target is the same physical core_marketplace table."""

    dependencies = [
        ("catalog", "0003_rename_master_tables"),
        ("marketplace", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="productvariantmarketplace",
                    name="marketplace",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="product_listings",
                        to="marketplace.marketplace",
                    ),
                ),
            ],
            database_operations=[],
        ),
    ]
