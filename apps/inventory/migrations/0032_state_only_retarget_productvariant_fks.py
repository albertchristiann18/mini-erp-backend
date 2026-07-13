import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """State-only migration: retarget the three inventory FK fields
    (ProductCogs.product_variant, ProductVariantWarehouse.product_variant,
    StockMovement.product_variant) from inventory.ProductVariant back to
    catalog.ProductVariant, then delete the inventory.ProductVariant proxy
    that was introduced by inventory/0028.

    This is the exact reverse of 0028's state operations:
    - AlterField x3: point FKs at catalog.productvariant (same as 0026)
    - DeleteModel: remove the proxy from inventory app state

    Zero DDL — the underlying DB column, constraint, and target table
    (master_productvariant, owned by catalog since inventory/0025) are
    physically unchanged.  SeparateDatabaseAndState(database_operations=[])
    ensures no SQL is emitted.  Mirrors the docstring and structure of
    inventory/0031_state_only_delete_productdimensionimage."""

    dependencies = [
        ("inventory", "0031_state_only_delete_productdimensionimage"),
        ("catalog", "0005_state_only_create_productdimensionimage"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="productcogs",
                    name="product_variant",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="catalog.productvariant",
                    ),
                ),
                migrations.AlterField(
                    model_name="productvariantwarehouse",
                    name="product_variant",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="warehouse_stocks",
                        to="catalog.productvariant",
                    ),
                ),
                migrations.AlterField(
                    model_name="stockmovement",
                    name="product_variant",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="catalog.productvariant",
                    ),
                ),
                migrations.DeleteModel(
                    name="ProductVariant",
                ),
            ],
            database_operations=[],
        ),
    ]
