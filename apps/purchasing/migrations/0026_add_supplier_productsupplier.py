import django_ulid.models
from django.db import migrations, models

import core.utils


class Migration(migrations.Migration):
    """State-only migration: register Supplier and ProductSupplier as belonging to
    apps.purchasing, and retarget PurchaseOrder.supplier from inventory.Supplier to
    purchasing.Supplier. Database tables (inventory_supplier, inventory_productsupplier)
    and the purchase_order.supplier FK constraint are left physically untouched."""

    dependencies = [
        ("catalog", "0003_rename_master_tables"),
        ("purchasing", "0025_noop_fk_refs"),
    ]

    _company_fk = models.ForeignKey(
        on_delete=models.deletion.CASCADE,
        to="core.Company",
        db_column="company_id",
    )

    state_operations = [
        migrations.CreateModel(
            name="Supplier",
            fields=[
                ("cdate", models.DateTimeField(auto_now_add=True)),
                ("udate", models.DateTimeField(auto_now=True)),
                (
                    "id",
                    django_ulid.models.ULIDField(
                        db_column="supplier_id",
                        default=core.utils.generate_ulid,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("company", _company_fk),
                ("name", models.CharField(max_length=255)),
                ("contact_name", models.CharField(blank=True, max_length=255, null=True)),
                ("phone", models.CharField(blank=True, max_length=50, null=True)),
                ("country", models.CharField(blank=True, max_length=100, null=True)),
                ("notes", models.TextField(blank=True, null=True)),
                ("supplier_link", models.URLField(blank=True, max_length=500, null=True)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "db_table": "inventory_supplier",
            },
        ),
        migrations.CreateModel(
            name="ProductSupplier",
            fields=[
                ("cdate", models.DateTimeField(auto_now_add=True)),
                ("udate", models.DateTimeField(auto_now=True)),
                (
                    "id",
                    django_ulid.models.ULIDField(
                        db_column="product_supplier_id",
                        default=core.utils.generate_ulid,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("company", _company_fk),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="product_suppliers",
                        to="catalog.Product",
                    ),
                ),
                (
                    "supplier",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="product_suppliers",
                        to="purchasing.Supplier",
                    ),
                ),
                (
                    "supplier_link",
                    models.URLField(
                        blank=True,
                        help_text="URL to this product on the supplier's site",
                        max_length=500,
                        null=True,
                    ),
                ),
            ],
            options={
                "db_table": "inventory_productsupplier",
                "unique_together": {("product", "supplier")},
            },
        ),
        migrations.AlterField(
            model_name="purchaseorder",
            name="supplier",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="purchase_orders",
                to="purchasing.Supplier",
            ),
        ),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=state_operations,
            database_operations=[],
        ),
    ]
