import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """State-only migration: retarget SalesOrder.marketplace FK from core.Marketplace to
    marketplace.Marketplace. The DB column and FK constraint are physically unchanged because
    the target is the same physical core_marketplace table."""

    dependencies = [
        ("sales", "0005_dependency_on_catalog_rename"),
        ("marketplace", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="salesorder",
                    name="marketplace",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="marketplace.marketplace",
                    ),
                ),
            ],
            database_operations=[],
        ),
    ]
