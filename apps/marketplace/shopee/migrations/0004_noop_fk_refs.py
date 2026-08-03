import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """State-only migration: retarget ShopeeShop.marketplace FK from core.Marketplace to
    marketplace.Marketplace. The DB column and FK constraint are physically unchanged because
    the target is the same physical core_marketplace table."""

    dependencies = [
        ("shopee", "0003_noop_fk_refs"),
        ("marketplace", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="shopeeshop",
                    name="marketplace",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="shopee_shops",
                        to="marketplace.marketplace",
                    ),
                ),
            ],
            database_operations=[],
        ),
    ]
