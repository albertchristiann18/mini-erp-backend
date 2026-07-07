from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("purchasing", "0015_purchaseorder_has_discount"),
    ]

    operations = [
        # Step 1: Create per-company PO number counter table
        migrations.RunSQL(
            sql="""
            CREATE TABLE po_number_counter (
                company_id UUID NOT NULL,
                year CHAR(4) NOT NULL,
                last_value INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (company_id, year)
            );
            """,
            reverse_sql="DROP TABLE IF EXISTS po_number_counter;",
        ),
        # Step 2: Seed counter table from existing PO numbers to preserve data
        migrations.RunSQL(
            sql="""
            INSERT INTO po_number_counter (company_id, year, last_value)
            SELECT
                company_id,
                SUBSTRING(purchase_order_number FROM 4 FOR 4) AS year,
                MAX(CAST(SUBSTRING(purchase_order_number FROM 9) AS INTEGER)) AS last_value
            FROM purchasing_purchaseorder
            WHERE purchase_order_number ~ '^PO-\\d{4}-\\d+$'
            GROUP BY company_id, SUBSTRING(purchase_order_number FROM 4 FOR 4);
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
        # Step 3: Drop old trigger, function, and global sequence
        migrations.RunSQL(
            sql="""
            DROP TRIGGER IF EXISTS trg_generate_po_number ON purchasing_purchaseorder;
            DROP FUNCTION IF EXISTS generate_po_number();
            DROP SEQUENCE IF EXISTS po_number_seq;
            """,
            reverse_sql="""
            CREATE SEQUENCE po_number_seq START WITH 1;

            CREATE OR REPLACE FUNCTION generate_po_number()
            RETURNS TRIGGER AS $$
            DECLARE
                current_year TEXT;
                seq_val INT;
                po_number TEXT;
            BEGIN
                current_year := TO_CHAR(NOW(), 'YYYY');
                seq_val := nextval('po_number_seq');
                po_number := 'PO-' || current_year || '-' || LPAD(seq_val::text, 3, '0');
                NEW.purchase_order_number := po_number;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER trg_generate_po_number
            BEFORE INSERT ON purchasing_purchaseorder
            FOR EACH ROW
            EXECUTE FUNCTION generate_po_number();
            """,
        ),
        # Step 4: Create new per-company trigger function and trigger
        migrations.RunSQL(
            sql="""
            CREATE OR REPLACE FUNCTION generate_po_number()
            RETURNS TRIGGER AS $$
            DECLARE
                current_year TEXT;
                seq_val INT;
                po_number TEXT;
            BEGIN
                current_year := TO_CHAR(NOW(), 'YYYY');

                INSERT INTO po_number_counter (company_id, year, last_value)
                VALUES (NEW.company_id, current_year, 1)
                ON CONFLICT (company_id, year) DO UPDATE
                    SET last_value = po_number_counter.last_value + 1
                RETURNING last_value INTO seq_val;

                po_number := 'PO-' || current_year || '-' || LPAD(seq_val::text, 3, '0');
                NEW.purchase_order_number := po_number;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER trg_generate_po_number
            BEFORE INSERT ON purchasing_purchaseorder
            FOR EACH ROW
            EXECUTE FUNCTION generate_po_number();
            """,
            reverse_sql="""
            DROP TRIGGER IF EXISTS trg_generate_po_number ON purchasing_purchaseorder;
            DROP FUNCTION IF EXISTS generate_po_number();
            """,
        ),
    ]
