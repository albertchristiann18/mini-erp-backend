from django.db import migrations


class Migration(migrations.Migration):
    """State-only migration: remove 5 models from inventory app state.
    Database tables remain intact (now owned by apps.catalog)."""

    dependencies = [
        ("catalog", "0001_initial"),
        ("inventory", "0024_conditional_product_sku_trigger"),
        ("purchasing", "0024_remove_sourcingpoolitem_pool_and_more"),
        ("sales", "0003_salesorder_source_platform"),
        ("shopee", "0002_add_shopee_stock_sync_log"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="ProductVariantMarketplace"),
                migrations.DeleteModel(name="ProductPhoto"),
                migrations.DeleteModel(name="ProductVariant"),
                migrations.DeleteModel(name="Product"),
                migrations.DeleteModel(name="Category"),
            ],
            database_operations=[],
        ),
    ]
