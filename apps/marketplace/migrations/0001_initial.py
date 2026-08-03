import django_ulid.models
from django.db import migrations, models

import core.utils


class Migration(migrations.Migration):
    """State-only migration: register Marketplace, MarketplaceConnection, CompanyMarketplace,
    BusinessEntity, and ProductBusinessEntity as belonging to apps.marketplace. Database tables
    (core_marketplace, marketplace_connection, inventory_companymarketplace,
    inventory_businessentity, inventory_productbusinessentity) are left physically untouched."""

    dependencies = [
        ("core", "0002_add_user_profile"),
        ("catalog", "0003_rename_master_tables"),
        ("shopee", "0003_noop_fk_refs"),
        ("tiktok", "0001_initial"),
    ]

    # DefaultModel-derived models have db_column='company_id'; MarketplaceConnection does not.
    _company_fk_with_col = models.ForeignKey(
        on_delete=models.deletion.CASCADE,
        to="core.Company",
        db_column="company_id",
    )
    _company_fk_no_col = models.ForeignKey(
        on_delete=models.deletion.CASCADE,
        related_name="marketplace_connections",
        to="core.Company",
    )

    state_operations = [
        migrations.CreateModel(
            name="Marketplace",
            fields=[
                ("cdate", models.DateTimeField(auto_now_add=True)),
                ("udate", models.DateTimeField(auto_now=True)),
                (
                    "id",
                    django_ulid.models.ULIDField(
                        db_column="marketplace_id",
                        default=core.utils.generate_ulid,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=255)),
                ("url", models.URLField(blank=True, null=True)),
                ("status", models.CharField(blank=True, max_length=50, null=True)),
                ("connected_time", models.DateTimeField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "shipping_config",
                    models.JSONField(
                        blank=True,
                        default=core.utils.get_default_shipping_config,
                        help_text="Stores marketplace-specific shipping and insurance settings",
                    ),
                ),
            ],
            options={
                "db_table": "core_marketplace",
            },
        ),
        migrations.CreateModel(
            name="MarketplaceConnection",
            fields=[
                ("cdate", models.DateTimeField(auto_now_add=True)),
                ("udate", models.DateTimeField(auto_now=True)),
                (
                    "id",
                    django_ulid.models.ULIDField(
                        db_column="connection_id",
                        default=core.utils.generate_ulid,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("company", _company_fk_no_col),
                (
                    "platform",
                    models.CharField(
                        choices=[("SHOPEE", "Shopee"), ("TIKTOK", "TikTok Shop")],
                        max_length=32,
                    ),
                ),
                (
                    "display_name",
                    models.CharField(
                        blank=True,
                        help_text="e.g. 'Brand A Shopee Official'",
                        max_length=255,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                (
                    "shopee_shop",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="connection",
                        to="shopee.shopeeshop",
                    ),
                ),
                (
                    "tiktok_shop",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="connection",
                        to="tiktok.tiktokshop",
                    ),
                ),
            ],
            options={
                "db_table": "marketplace_connection",
                "unique_together": {("company", "platform", "display_name")},
            },
        ),
        migrations.CreateModel(
            name="CompanyMarketplace",
            fields=[
                ("cdate", models.DateTimeField(auto_now_add=True)),
                ("udate", models.DateTimeField(auto_now=True)),
                ("company", _company_fk_with_col),
                (
                    "id",
                    django_ulid.models.ULIDField(
                        db_column="company_marketplace_id",
                        default=core.utils.generate_ulid,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=255)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "db_table": "inventory_companymarketplace",
                "unique_together": {("company", "name")},
            },
        ),
        migrations.CreateModel(
            name="BusinessEntity",
            fields=[
                ("cdate", models.DateTimeField(auto_now_add=True)),
                ("udate", models.DateTimeField(auto_now=True)),
                ("company", _company_fk_with_col),
                (
                    "id",
                    django_ulid.models.ULIDField(
                        db_column="business_entity_id",
                        default=core.utils.generate_ulid,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=255)),
                (
                    "marketplace",
                    models.ForeignKey(
                        on_delete=models.deletion.PROTECT,
                        related_name="business_entities",
                        to="marketplace.companymarketplace",
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "db_table": "inventory_businessentity",
                "unique_together": {("company", "name")},
            },
        ),
        migrations.CreateModel(
            name="ProductBusinessEntity",
            fields=[
                ("cdate", models.DateTimeField(auto_now_add=True)),
                ("udate", models.DateTimeField(auto_now=True)),
                ("company", _company_fk_with_col),
                (
                    "id",
                    django_ulid.models.ULIDField(
                        db_column="product_business_entity_id",
                        default=core.utils.generate_ulid,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="business_entities",
                        to="catalog.product",
                    ),
                ),
                (
                    "business_entity",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="product_assignments",
                        to="marketplace.businessentity",
                    ),
                ),
            ],
            options={
                "db_table": "inventory_productbusinessentity",
                "unique_together": {("product", "business_entity")},
            },
        ),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=state_operations,
            database_operations=[],
        ),
    ]
