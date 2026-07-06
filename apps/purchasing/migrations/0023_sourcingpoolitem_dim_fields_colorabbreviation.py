import django.db.models.deletion
import django.utils.timezone
import django_ulid.models
from django.db import migrations, models

import core.utils


class Migration(migrations.Migration):
    """Add dimension fields to SourcingPoolItem and create ColorAbbreviation model."""

    dependencies = [
        ("core", "0002_add_user_profile"),
        ("purchasing", "0022_remove_purchaseorderdetail_draft_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcingpoolitem",
            name="dim1_key",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="sourcingpoolitem",
            name="dim1_value",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="sourcingpoolitem",
            name="dim2_key",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="sourcingpoolitem",
            name="dim2_value",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="sourcingpoolitem",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="sourcingpoolitem",
            name="is_used",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="sourcingpoolitem",
            name="last_active_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AddField(
            model_name="sourcingpoolitem",
            name="used_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sourcingpoolitem",
            name="used_in_po",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="used_pool_items",
                to="purchasing.purchaseorder",
            ),
        ),
        migrations.CreateModel(
            name="ColorAbbreviation",
            fields=[
                ("cdate", models.DateTimeField(auto_now_add=True)),
                ("udate", models.DateTimeField(auto_now=True)),
                (
                    "id",
                    django_ulid.models.ULIDField(
                        db_column="color_abbreviation_id",
                        default=core.utils.generate_ulid,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("color_name", models.CharField(max_length=100)),
                ("abbreviation", models.CharField(max_length=20)),
                (
                    "company",
                    models.ForeignKey(
                        db_column="company_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        to="core.company",
                    ),
                ),
            ],
            options={
                "unique_together": {("company", "color_name")},
            },
        ),
    ]
