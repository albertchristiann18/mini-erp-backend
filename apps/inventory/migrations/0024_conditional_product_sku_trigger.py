from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0023_product_dim1_key_product_dim1_options_and_more"),
    ]
    operations = [
        migrations.RunSQL(
            sql="""
            CREATE OR REPLACE FUNCTION generate_product_sku()
            RETURNS TRIGGER AS $$
            DECLARE
                cat_code TEXT;
                seq_val INT;
            BEGIN
                IF NEW.sku_code IS NULL OR NEW.sku_code = '' THEN
                    seq_val := nextval('product_sku_seq');
                    SELECT category_code INTO cat_code
                    FROM inventory_category WHERE category_id = NEW.category_id;
                    NEW.sku_code := UPPER(cat_code) || '-' || LPAD(seq_val::text, 3, '0');
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
            reverse_sql="""
            CREATE OR REPLACE FUNCTION generate_product_sku()
            RETURNS TRIGGER AS $$
            DECLARE
                cat_code TEXT;
                seq_val INT;
            BEGIN
                seq_val := nextval('product_sku_seq');
                SELECT category_code INTO cat_code
                FROM inventory_category WHERE category_id = NEW.category_id;
                NEW.sku_code := UPPER(cat_code) || '-' || LPAD(seq_val::text, 3, '0');
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        )
    ]
