from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0009_productcogs_allocated_commission_fee"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE OR REPLACE FUNCTION generate_variant_sku()
            RETURNS TRIGGER AS $$
            DECLARE
                parent_sku TEXT;
                val1 TEXT;
                val2 TEXT;
            BEGIN
                IF NEW.sku_variant_code IS NULL OR NEW.sku_variant_code = '' THEN
                    SELECT sku_code INTO parent_sku 
                    FROM inventory_product WHERE product_id = NEW.product_id;

                    val1 := NEW.variant_values->>'1';
                    val2 := NEW.variant_values->>'2';

                    NEW.sku_variant_code := parent_sku;
                    
                    IF val1 IS NOT NULL AND val1 != '' THEN
                        NEW.sku_variant_code := NEW.sku_variant_code || '-' || UPPER(val1);
                    END IF;
                    
                    IF val2 IS NOT NULL AND val2 != '' THEN
                        NEW.sku_variant_code := NEW.sku_variant_code || '-' || UPPER(val2);
                    END IF;
                END IF;

                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
            reverse_sql="""
            CREATE OR REPLACE FUNCTION generate_variant_sku()
            RETURNS TRIGGER AS $$
            DECLARE
                parent_sku TEXT;
                val1 TEXT;
                val2 TEXT;
            BEGIN
                SELECT sku_code INTO parent_sku 
                FROM inventory_product WHERE product_id = NEW.product_id;

                val1 := NEW.variant_values->>'1';
                val2 := NEW.variant_values->>'2';

                NEW.sku_variant_code := parent_sku;
                
                IF val1 IS NOT NULL AND val1 != '' THEN
                    NEW.sku_variant_code := NEW.sku_variant_code || '-' || UPPER(val1);
                END IF;
                
                IF val2 IS NOT NULL AND val2 != '' THEN
                    NEW.sku_variant_code := NEW.sku_variant_code || '-' || UPPER(val2);
                END IF;

                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
    ]
