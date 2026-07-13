import django.db.models.deletion
import django_ulid.models
from django.db import migrations, models

import core.utils


class Migration(migrations.Migration):
    """State-only migration: register ProductDimensionImage as belonging to apps.catalog.
    The physical table (inventory_productdimensionimage) is left untouched — db_table pinned
    to keep the same physical name. Zero DDL emitted.

    MUST depend on inventory/0026_noop_fk_refs which already retargeted
    ProductDimensionImage.product → catalog.product, so the FK to="catalog.product"
    in this CreateModel is consistent with migration graph state at that point.
    """

    dependencies = [
        ("catalog", "0004_noop_fk_refs"),
        ("core", "0002_add_user_profile"),
        ("inventory", "0026_noop_fk_refs"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="ProductDimensionImage",
                    fields=[
                        ("cdate", models.DateTimeField(auto_now_add=True)),
                        ("udate", models.DateTimeField(auto_now=True)),
                        (
                            "id",
                            django_ulid.models.ULIDField(
                                db_column="product_dim_image_id",
                                default=core.utils.generate_ulid,
                                editable=False,
                                primary_key=True,
                                serialize=False,
                            ),
                        ),
                        (
                            "company",
                            models.ForeignKey(
                                db_column="company_id",
                                on_delete=django.db.models.deletion.CASCADE,
                                to="core.company",
                            ),
                        ),
                        (
                            "product",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="dimension_images",
                                to="catalog.product",
                            ),
                        ),
                        ("dim_key", models.CharField(max_length=100)),
                        ("dim_value", models.CharField(max_length=100)),
                        ("photo", models.FileField(upload_to="variants/dimension_images/")),
                    ],
                    options={
                        "db_table": "inventory_productdimensionimage",
                        "unique_together": {("product", "dim_key", "dim_value")},
                    },
                ),
            ],
            database_operations=[],
        ),
    ]
