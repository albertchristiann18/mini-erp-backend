from django.db import migrations


class Migration(migrations.Migration):
    """State-only migration: remove Marketplace and MarketplaceConnection from core app state.
    Database tables (core_marketplace, marketplace_connection) and every FK constraint against
    them are left physically untouched (now owned by apps.marketplace).

    MUST depend on marketplace/0001_initial so that on a from-scratch replay the DeleteModel
    never runs before marketplace has adopted the models — the exact BE2 ordering-bug class."""

    dependencies = [
        ("core", "0002_add_user_profile"),
        ("marketplace", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="MarketplaceConnection"),
                migrations.DeleteModel(name="Marketplace"),
            ],
            database_operations=[],
        ),
    ]
