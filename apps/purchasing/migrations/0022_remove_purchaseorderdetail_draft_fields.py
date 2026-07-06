from django.db import migrations


class Migration(migrations.Migration):
    """Remove draft fields from PurchaseOrderDetail.

    Deletes existing draft lines (product_variant IS NULL) before removing
    the sourcing_item FK so the FK still exists at delete time.
    """

    dependencies = [
        ("purchasing", "0021_remove_sourcingpoolitem_unique_pool_supplier_link_variant_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            sql="DELETE FROM purchasing_purchaseorderdetail WHERE product_variant_id IS NULL;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RemoveField(
            model_name="purchaseorderdetail",
            name="draft_product_name",
        ),
        migrations.RemoveField(
            model_name="purchaseorderdetail",
            name="sourcing_item",
        ),
    ]
