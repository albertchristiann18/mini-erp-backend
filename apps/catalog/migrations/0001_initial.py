import django_ulid.models
from django.db import migrations, models

import core.utils


def _ulid_field(**overrides):
    return django_ulid.models.ULIDField(
        primary_key=True,
        default=core.utils.generate_ulid,
        editable=False,
        **overrides,
    )


class Migration(migrations.Migration):
    """State-only migration: register 5 models as belonging to apps.catalog.
    Database tables already exist under inventory_* / product_photo names."""

    dependencies = [
        ("core", "0002_add_user_profile"),
        ("inventory", "0024_conditional_product_sku_trigger"),
    ]

    _company_fk = models.ForeignKey(
        on_delete=models.deletion.CASCADE,
        to="core.Company",
        db_column="company_id",
    )

    state_operations = [
        migrations.CreateModel(
            name="ProductVariantMarketplace",
            fields=[
                ("cdate", models.DateTimeField(auto_now_add=True)),
                ("udate", models.DateTimeField(auto_now=True)),
                ("id", _ulid_field(db_column="product_variant_marketplace_id")),
                ("company", _company_fk),
                ("selling_price", models.BigIntegerField()),
                ("discounted_price", models.BigIntegerField(null=True, blank=True)),
                ("shopee_item_id", models.BigIntegerField(null=True, blank=True)),
                ("shopee_model_id", models.BigIntegerField(null=True, blank=True)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "db_table": "inventory_productvariantmarketplace",
                "unique_together": {("product_variant", "marketplace")},
            },
        ),
        migrations.CreateModel(
            name="Category",
            fields=[
                ("cdate", models.DateTimeField(auto_now_add=True)),
                ("udate", models.DateTimeField(auto_now=True)),
                ("id", _ulid_field(db_column="category_id")),
                ("company", _company_fk),
                ("name", models.CharField(max_length=255, unique=True)),
                ("category_code", models.CharField(max_length=100, unique=True)),
                ("description", models.TextField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("master_category_key", models.CharField(blank=True, default="", max_length=100)),
                ("shopee_category_id", models.BigIntegerField(blank=True, null=True)),
            ],
            options={
                "db_table": "inventory_category",
            },
        ),
        migrations.CreateModel(
            name="Product",
            fields=[
                ("cdate", models.DateTimeField(auto_now_add=True)),
                ("udate", models.DateTimeField(auto_now=True)),
                ("id", _ulid_field(db_column="product_id")),
                ("company", _company_fk),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True, null=True)),
                ("sku_code", models.CharField(editable=False, max_length=100, unique=True)),
                ("total_qty", models.IntegerField(default=0)),
                ("total_cogs", models.IntegerField(default=0)),
                ("variant_options", models.JSONField(blank=True, default=list)),
                ("specifications", models.JSONField(blank=True, default=dict)),
                (
                    "product_photo",
                    models.FileField(blank=True, null=True, upload_to="products/photos/"),
                ),
                ("weight", models.IntegerField(default=0)),
                ("length", models.IntegerField(default=0)),
                ("width", models.IntegerField(default=0)),
                ("height", models.IntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "shipping_config",
                    models.JSONField(
                        blank=True,
                        default=core.utils.get_default_shipping_config,
                        help_text="Stores marketplace-specific shipping and insurance settings",
                    ),
                ),
                ("dim1_key", models.CharField(blank=True, default="", max_length=100)),
                ("dim2_key", models.CharField(blank=True, default="", max_length=100)),
                ("dim1_options", models.JSONField(blank=True, default=list)),
                ("dim2_options", models.JSONField(blank=True, default=list)),
            ],
            options={
                "db_table": "inventory_product",
            },
        ),
        migrations.CreateModel(
            name="ProductPhoto",
            fields=[
                ("cdate", models.DateTimeField(auto_now_add=True)),
                ("udate", models.DateTimeField(auto_now=True)),
                ("id", _ulid_field(db_column="product_photo_id")),
                ("company", _company_fk),
                ("image", models.FileField(upload_to="products/photos/")),
                ("order", models.PositiveSmallIntegerField(default=0)),
                ("is_primary", models.BooleanField(default=False)),
            ],
            options={
                "db_table": "product_photo",
                "ordering": ["order"],
            },
        ),
        migrations.CreateModel(
            name="ProductVariant",
            fields=[
                ("cdate", models.DateTimeField(auto_now_add=True)),
                ("udate", models.DateTimeField(auto_now=True)),
                ("id", _ulid_field(db_column="product_variant_id")),
                ("company", _company_fk),
                ("name", models.CharField(max_length=255)),
                ("sku_variant_code", models.CharField(max_length=100, unique=True)),
                ("variant_values", models.JSONField(default=dict)),
                ("current_cogs", models.BigIntegerField(default=0)),
                ("base_price", models.BigIntegerField(default=0)),
                ("total_incoming_qty", models.IntegerField(default=0)),
                ("total_outgoing_qty", models.IntegerField(default=0)),
                ("total_available_qty", models.IntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("is_fake", models.BooleanField(default=False)),
                ("photo", models.FileField(blank=True, null=True, upload_to="variants/photos/")),
                (
                    "last_unit_price_foreign",
                    models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True),
                ),
                ("last_currency", models.CharField(blank=True, max_length=10, null=True)),
                (
                    "last_discounted_unit_price_foreign",
                    models.DecimalField(blank=True, decimal_places=3, max_digits=15, null=True),
                ),
                ("price_updated_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "inventory_productvariant",
            },
        ),
        migrations.AddField(
            model_name="productvariantmarketplace",
            name="marketplace",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="product_listings",
                to="core.Marketplace",
            ),
        ),
        migrations.AddField(
            model_name="productvariantmarketplace",
            name="product_variant",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="marketplace_listings",
                to="catalog.ProductVariant",
            ),
        ),
        migrations.AddField(
            model_name="productvariant",
            name="product",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="variants",
                to="catalog.Product",
            ),
        ),
        migrations.AddField(
            model_name="productphoto",
            name="product",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="photos",
                to="catalog.Product",
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="category",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="products",
                to="catalog.Category",
            ),
        ),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=state_operations,
            database_operations=[],
        ),
    ]
