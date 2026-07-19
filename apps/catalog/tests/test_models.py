from django.db import connection
from django.test import TestCase

from apps.catalog.tests.factories import CategoryFactory, ProductFactory, ProductVariantFactory
from core.factories import CompanyFactory
from core.utils import generate_ulid


class SKUTriggerRegressionTests(TestCase):
    """Regression tests for trigger functions after renaming tables to master_*.

    These tests verify that the PL/pgSQL trigger functions that auto-generate SKU codes
    still fire correctly after the table rename migrations (catalog/0003, inventory/0027).
    """

    def setUp(self):
        self.company = CompanyFactory()
        self.category = CategoryFactory(company=self.company, category_code="TSH")

    def test_product_sku_trigger_fires_against_renamed_category_table(self):
        """DB trigger auto-generates Product.sku_code by reading master_category."""
        # Create a valid product via ORM (gets a real sku_code from the trigger)
        template = ProductFactory(company=self.company, category=self.category)
        # The trigger mutates sku_code inside Postgres via a plain INSERT with no
        # RETURNING clause -- Django never re-reads it back into the in-memory
        # instance, so it must be explicitly refreshed before asserting on it.
        template.refresh_from_db()
        self.assertIsNotNone(template.sku_code)
        self.assertTrue(template.sku_code.startswith("TSH-"))

        # Read the template row's actual columns from the database. product_id is a
        # Postgres uuid column (ULIDField.get_internal_type() == "UUIDField") -- pass
        # the real uuid.UUID via .uuid, not str(), which yields the 26-char base32
        # ULID form Postgres cannot cast to uuid.
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM master_product WHERE product_id = %s", [template.pk.uuid])
            columns = [col[0] for col in cursor.description]
            values = list(cursor.fetchone())

        # Clone the row: map column names to values
        col_value_map = dict(zip(columns, values))
        new_pk = generate_ulid().uuid  # same uuid-cast requirement as above
        col_value_map["product_id"] = new_pk
        col_value_map["sku_code"] = ""  # Blank triggers the DB trigger function
        col_value_map["name"] = "Trigger Test Product Master Rename"

        # Insert via raw SQL, bypassing ORM
        cols = list(col_value_map.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO master_product ({', '.join(cols)}) VALUES ({placeholders})",
                list(col_value_map.values()),
            )
            # Verify the trigger generated a sku_code
            cursor.execute("SELECT sku_code FROM master_product WHERE product_id = %s", [new_pk])
            result_sku_code = cursor.fetchone()[0]

        # The trigger should have read master_category and generated a code
        self.assertIsNotNone(result_sku_code)
        self.assertTrue(result_sku_code.startswith(self.category.category_code.upper()))

    def test_variant_sku_trigger_fires_against_renamed_product_table(self):
        """DB trigger auto-generates ProductVariant.sku_variant_code by reading master_product."""
        # Create a valid variant via ORM (gets a real sku_variant_code from the trigger)
        product = ProductFactory(company=self.company, category=self.category)
        product.refresh_from_db()  # re-read the trigger-generated sku_code before using it below
        template = ProductVariantFactory(product=product, sku_variant_code="")
        template.refresh_from_db()
        self.assertIsNotNone(template.sku_variant_code)
        self.assertTrue(template.sku_variant_code.startswith(product.sku_code))

        # Read the template row's actual columns from the database. product_variant_id is
        # a Postgres uuid column -- pass the real uuid.UUID via .uuid, not str(), which
        # yields the 26-char base32 ULID form Postgres cannot cast to uuid.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM master_productvariant WHERE product_variant_id = %s",
                [template.pk.uuid],
            )
            columns = [col[0] for col in cursor.description]
            values = list(cursor.fetchone())

        # Clone the row, but point it at a SECOND, independently-created parent product.
        # generate_variant_sku() has no uniqueness source of its own (unlike
        # generate_product_sku(), which uses product_sku_seq) -- with variant_values={} on
        # both rows, jsonb_each_text('{}'::jsonb) returns zero rows so string_agg yields NULL
        # and the suffix branch never fires, meaning the generated sku_variant_code is always
        # exactly the parent's bare sku_code. Cloning against the SAME parent as `template`
        # would therefore always collide on ProductVariant.sku_variant_code's unique
        # constraint. Using a different parent (whose sku_code is guaranteed unique via
        # Product's own sequence-backed trigger) makes the two generated codes provably
        # distinct without relying on any jsonb suffix behavior.
        second_product = ProductFactory(company=self.company, category=self.category)
        second_product.refresh_from_db()

        # Clone the row: map column names to values
        col_value_map = dict(zip(columns, values))
        new_pk = generate_ulid().uuid  # same uuid-cast requirement as above
        col_value_map["product_variant_id"] = new_pk
        col_value_map["product_id"] = second_product.pk.uuid  # different parent, same reason
        col_value_map["sku_variant_code"] = ""  # Blank triggers the DB trigger function
        col_value_map["name"] = "Trigger Test Variant Master Rename"

        # Insert via raw SQL, bypassing ORM
        cols = list(col_value_map.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO master_productvariant ({', '.join(cols)}) VALUES ({placeholders})",
                list(col_value_map.values()),
            )
            # Verify the trigger generated a sku_variant_code
            cursor.execute(
                "SELECT sku_variant_code FROM master_productvariant WHERE product_variant_id = %s",
                [new_pk],
            )
            result_sku_variant_code = cursor.fetchone()[0]

        # The trigger should have read master_product for THIS row's own product_id
        # (second_product, not the original `product`) and generated a matching code.
        self.assertIsNotNone(result_sku_variant_code)
        self.assertTrue(result_sku_variant_code.startswith(second_product.sku_code))
        self.assertNotEqual(result_sku_variant_code, template.sku_variant_code)
