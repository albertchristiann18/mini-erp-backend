"""
Tests for the master-data import parser and CatalogImportService.

No .xlsx fixture files — all rows are constructed in-memory.
"""

from decimal import Decimal

from django.test import TestCase

from apps.catalog.models import Category, Product, ProductVariant
from apps.catalog.services.catalog_import_service import CatalogImportService
from apps.inventory.models import ProductVariantWarehouse, Warehouse
from apps.purchasing.models import ProductSupplier, Supplier
from core.factories import CompanyFactory
from core.management.commands.import_master_data_parser import (
    MasterSkuRow,
    VariantRow,
    build_variant_options,
    parse_master_sku_sheet,
    parse_variant_sheet,
)


# ---------------------------------------------------------------------------
# Helpers — build raw tuple rows that match the Excel column layout
# ---------------------------------------------------------------------------
def _master_sku_header() -> tuple:
    return tuple(f"col{i}" for i in range(12))


def _master_sku_row(
    sku_code: str = "SEG-001",
    category_code: str = "SEG",
    product_name: str = "Test Product",
    supplier_name: str = "Supplier A",
) -> tuple:
    """Build a raw tuple that matches the Master SKU column layout (0-indexed)."""
    row = [None] * 12
    row[4] = supplier_name  # col[4] = supplier_name
    row[5] = category_code  # col[5] = category_code
    row[7] = sku_code  # col[7] = sku_code
    row[9] = product_name  # col[9] = product_name
    return tuple(row)


def _variant_row(
    sku_code: str = "SEG-001",
    sku_variant_code: str = "SEG-001-12-BLK",
    color_code: str = "blk",
    product_name: str = "Test Product",
    supplier_name: str = "Supplier A",
    color_display: str = "Black",
    size_code: str = "12",
    cogs: object = "50000",
    base_price: object = "100000",
    stock_qty: object = 10,
) -> tuple:
    """Build a raw tuple that matches the Master SKU Variant column layout (0-indexed)."""
    row = [None] * 28
    row[2] = color_code  # col[2] = color_code
    row[5] = sku_code  # col[5] = sku_code
    row[6] = sku_variant_code  # col[6] = sku_variant_code
    row[7] = product_name  # col[7] = product_name
    row[8] = supplier_name  # col[8] = supplier_name
    row[9] = color_display  # col[9] = color_display
    row[12] = size_code  # col[12] = size_code
    row[13] = cogs  # col[13] = cogs
    row[19] = base_price  # col[19] = base_price
    row[27] = stock_qty  # col[27] = stock_qty
    return tuple(row)


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------
class TestParseMasterSkuSheet(TestCase):
    def test_parse_master_sku_sheet_happy_path(self):
        """Valid rows parse into MasterSkuRow dataclasses."""
        rows = [
            _master_sku_header(),
            _master_sku_row("SEG-001", "SEG", "Setelan Anak", "Supplier A"),
        ]
        result = parse_master_sku_sheet(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].sku_code, "SEG-001")
        self.assertEqual(result[0].category_code, "SEG")
        self.assertEqual(result[0].product_name, "Setelan Anak")
        self.assertEqual(result[0].supplier_name, "Supplier A")

    def test_parse_master_sku_sheet_skips_blank_sku_code(self):
        """Rows with a blank sku_code are silently skipped."""
        rows = [
            _master_sku_header(),
            _master_sku_row("", "SEG", "No SKU Product", "Supplier A"),
            _master_sku_row("SEG-002", "SEG", "Good Product", "Supplier A"),
        ]
        result = parse_master_sku_sheet(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].sku_code, "SEG-002")

    def test_parse_master_sku_sheet_skips_none_sku_code(self):
        """Rows where sku_code column is None are silently skipped."""
        rows = [
            _master_sku_header(),
            _master_sku_row(None, "SEG", "No SKU Product", "Supplier A"),  # type: ignore[arg-type]
        ]
        result = parse_master_sku_sheet(rows)
        self.assertEqual(len(result), 0)

    def test_parse_master_sku_sheet_handles_blank_supplier(self):
        """Blank supplier_name is allowed (stored as empty string)."""
        rows = [
            _master_sku_header(),
            _master_sku_row("SEG-003", "SEG", "No Supplier Product", ""),
        ]
        result = parse_master_sku_sheet(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].supplier_name, "")

    def test_parse_master_sku_sheet_strips_whitespace(self):
        """Leading/trailing whitespace is stripped from all string fields."""
        rows = [
            _master_sku_header(),
            _master_sku_row("  SEG-004  ", "  SEG  ", "  Padded  ", "  Supplier B  "),
        ]
        result = parse_master_sku_sheet(rows)
        self.assertEqual(result[0].sku_code, "SEG-004")
        self.assertEqual(result[0].category_code, "SEG")
        self.assertEqual(result[0].product_name, "Padded")
        self.assertEqual(result[0].supplier_name, "Supplier B")

    def test_parse_master_sku_sheet_header_is_skipped(self):
        """The first row (header) is always skipped."""
        rows = [_master_sku_header()]
        result = parse_master_sku_sheet(rows)
        self.assertEqual(len(result), 0)


class TestParseVariantSheet(TestCase):
    def test_parse_variant_sheet_happy_path(self):
        """Valid rows parse into VariantRow dataclasses."""
        rows = [
            ("header",),
            _variant_row(
                "SEG-001",
                "SEG-001-12-BLK",
                "blk",
                "Test",
                "Sup A",
                "Black",
                "12",
                "50000",
                "100000",
                10,
            ),
        ]
        result = parse_variant_sheet(rows)
        self.assertEqual(len(result), 1)
        r = result[0]
        self.assertEqual(r.sku_code, "SEG-001")
        self.assertEqual(r.sku_variant_code, "SEG-001-12-BLK")
        self.assertEqual(r.color_code, "blk")
        self.assertEqual(r.color_display, "Black")
        self.assertEqual(r.size_code, "12")
        self.assertEqual(r.cogs, Decimal("50000"))
        self.assertEqual(r.base_price, Decimal("100000"))
        self.assertEqual(r.stock_qty, 10)

    def test_parse_variant_sheet_skips_blank_sku_variant_code(self):
        """Rows with blank sku_variant_code are silently skipped."""
        rows = [
            ("header",),
            _variant_row(sku_variant_code=""),
            _variant_row(sku_variant_code="SEG-001-14-BLK"),
        ]
        result = parse_variant_sheet(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].sku_variant_code, "SEG-001-14-BLK")

    def test_parse_variant_sheet_skips_blank_sku_code(self):
        """Rows with blank sku_code are silently skipped."""
        rows = [
            ("header",),
            _variant_row(sku_code="", sku_variant_code="SEG-001-12-RED"),
            _variant_row(sku_code="SEG-001", sku_variant_code="SEG-001-12-BLK"),
        ]
        result = parse_variant_sheet(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].sku_code, "SEG-001")

    def test_parse_variant_sheet_malformed_cogs_raises_value_error(self):
        """A non-numeric cogs value raises ValueError with a descriptive message."""
        rows = [
            ("header",),
            _variant_row(cogs="not-a-number"),
        ]
        with self.assertRaises(ValueError) as ctx:
            parse_variant_sheet(rows)
        self.assertIn("cogs", str(ctx.exception))

    def test_parse_variant_sheet_malformed_base_price_raises_value_error(self):
        """A non-numeric base_price value raises ValueError."""
        rows = [
            ("header",),
            _variant_row(base_price="abc"),
        ]
        with self.assertRaises(ValueError) as ctx:
            parse_variant_sheet(rows)
        self.assertIn("base_price", str(ctx.exception))

    def test_parse_variant_sheet_handles_blank_color_and_supplier(self):
        """Blank color_code and supplier_name are allowed (stored as empty string)."""
        rows = [
            ("header",),
            _variant_row(color_code="", supplier_name=""),
        ]
        result = parse_variant_sheet(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].color_code, "")
        self.assertEqual(result[0].supplier_name, "")

    def test_parse_variant_sheet_decimal_precision(self):
        """cogs and base_price are stored as Decimal, not float."""
        rows = [
            ("header",),
            _variant_row(cogs="49999.99", base_price="99999.50"),
        ]
        result = parse_variant_sheet(rows)
        self.assertIsInstance(result[0].cogs, Decimal)
        self.assertIsInstance(result[0].base_price, Decimal)
        self.assertEqual(result[0].cogs, Decimal("49999.99"))
        self.assertEqual(result[0].base_price, Decimal("99999.50"))

    def test_parse_variant_sheet_none_cogs_defaults_to_zero(self):
        """None cogs defaults to Decimal('0')."""
        rows = [
            ("header",),
            _variant_row(cogs=None),
        ]
        result = parse_variant_sheet(rows)
        self.assertEqual(result[0].cogs, Decimal("0"))

    def test_parse_variant_sheet_float_size_code_converted_to_int_string(self):
        """Float size codes like 12.0 are converted to '12', not '12.0'."""
        rows = [
            ("header",),
            _variant_row(size_code=12.0),
        ]
        result = parse_variant_sheet(rows)
        self.assertEqual(result[0].size_code, "12")


class TestBuildVariantOptions(TestCase):
    def _make_variant(self, size_code: str, color_code: str, color_display: str) -> VariantRow:
        return VariantRow(
            sku_code="SEG-001",
            sku_variant_code=f"SEG-001-{size_code}-{color_code}",
            color_code=color_code,
            product_name="Test",
            supplier_name="",
            color_display=color_display,
            size_code=size_code,
            cogs=Decimal("0"),
            base_price=Decimal("0"),
            stock_qty=0,
        )

    def test_build_variant_options_sizes_sorted_numerically(self):
        """Sizes are sorted numerically when they are digits."""
        variants = [
            self._make_variant("14", "blk", "Black"),
            self._make_variant("10", "blk", "Black"),
            self._make_variant("12", "blk", "Black"),
        ]
        _, dim1_options, _ = build_variant_options(variants)
        self.assertEqual(dim1_options, ["10", "12", "14"])

    def test_build_variant_options_colors_sorted_alphabetically(self):
        """Colors are sorted alphabetically by color_code."""
        variants = [
            self._make_variant("12", "wht", "White"),
            self._make_variant("12", "blk", "Black"),
            self._make_variant("12", "red", "Red"),
        ]
        _, _, dim2_options = build_variant_options(variants)
        self.assertEqual(dim2_options, ["blk", "red", "wht"])

    def test_build_variant_options_structure(self):
        """variant_options contains size and color entries with correct structure."""
        variants = [self._make_variant("12", "blk", "Black")]
        vo, _, _ = build_variant_options(variants)
        self.assertEqual(len(vo), 2)
        self.assertEqual(vo[0]["id"], "size")
        self.assertEqual(vo[1]["id"], "color")
        self.assertEqual(vo[0]["values"], [{"id": "12", "label": "12"}])
        self.assertEqual(vo[1]["values"], [{"id": "blk", "label": "Black"}])

    def test_build_variant_options_empty_list(self):
        """Empty variant list returns empty dim options."""
        vo, dim1, dim2 = build_variant_options([])
        self.assertEqual(dim1, [])
        self.assertEqual(dim2, [])

    def test_build_variant_options_deduplicates_sizes(self):
        """Duplicate size_codes appear only once in dim1_options."""
        variants = [
            self._make_variant("12", "blk", "Black"),
            self._make_variant("12", "wht", "White"),
        ]
        _, dim1_options, _ = build_variant_options(variants)
        self.assertEqual(dim1_options.count("12"), 1)


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------
class TestCatalogImportServiceHappyPath(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.service = CatalogImportService()

    def _run_import(self) -> None:
        master_rows = [
            MasterSkuRow("SEG-001", "SEG", "Setelan Anak", "Supplier A"),
        ]
        variant_rows = [
            VariantRow(
                sku_code="SEG-001",
                sku_variant_code="SEG-001-12-BLK",
                color_code="blk",
                product_name="Setelan Anak",
                supplier_name="Supplier A",
                color_display="Black",
                size_code="12",
                cogs=Decimal("50000"),
                base_price=Decimal("100000"),
                stock_qty=10,
            )
        ]
        return self.service.import_master_data(
            company=self.company,
            master_sku_rows=master_rows,
            variant_rows=variant_rows,
        )

    def test_import_creates_warehouse(self):
        """import_master_data creates a warehouse if it doesn't exist."""
        self._run_import()
        self.assertTrue(
            Warehouse.objects.filter(name="Master Warehouse", company=self.company).exists()
        )

    def test_import_creates_supplier(self):
        """import_master_data creates supplier records from supplier names."""
        self._run_import()
        self.assertTrue(Supplier.objects.filter(name="Supplier A", company=self.company).exists())

    def test_import_creates_category_seg(self):
        """import_master_data creates the SEG category with correct metadata."""
        self._run_import()
        cat = Category.objects.get(category_code="SEG")
        self.assertEqual(cat.name, "Set / Setelan")
        self.assertEqual(cat.shopee_category_id, 525)

    def test_import_creates_product(self):
        """import_master_data creates the product with correct sku_code."""
        self._run_import()
        self.assertTrue(Product.objects.filter(sku_code="SEG-001", company=self.company).exists())

    def test_import_creates_product_variant(self):
        """import_master_data creates the ProductVariant with correct sku_variant_code."""
        self._run_import()
        self.assertTrue(
            ProductVariant.objects.filter(
                sku_variant_code="SEG-001-12-BLK", company=self.company
            ).exists()
        )

    def test_import_sets_variant_cogs_as_integer(self):
        """current_cogs on the variant is set from the cogs Decimal value."""
        self._run_import()
        v = ProductVariant.objects.get(sku_variant_code="SEG-001-12-BLK")
        self.assertEqual(v.current_cogs, 50000)

    def test_import_creates_warehouse_stock_row(self):
        """import_master_data creates a ProductVariantWarehouse row for each variant."""
        self._run_import()
        variant = ProductVariant.objects.get(sku_variant_code="SEG-001-12-BLK")
        warehouse = Warehouse.objects.get(name="Master Warehouse", company=self.company)
        self.assertTrue(
            ProductVariantWarehouse.objects.filter(
                product_variant=variant, warehouse=warehouse
            ).exists()
        )

    def test_import_creates_product_supplier_link(self):
        """import_master_data creates a ProductSupplier link for products with supplier names."""
        self._run_import()
        product = Product.objects.get(sku_code="SEG-001", company=self.company)
        supplier = Supplier.objects.get(name="Supplier A", company=self.company)
        self.assertTrue(ProductSupplier.objects.filter(product=product, supplier=supplier).exists())

    def test_import_result_counts_correct(self):
        """ImportResult dataclass carries correct creation counts."""
        result = self._run_import()
        self.assertEqual(result.suppliers_created, 1)
        self.assertEqual(result.categories_created, 1)
        self.assertEqual(result.products_created, 1)
        self.assertEqual(result.variants_created, 1)
        self.assertEqual(result.warehouse_stock_created, 1)
        self.assertEqual(result.product_suppliers_created, 1)

    def test_import_warehouse_stock_physical_qty_set(self):
        """ProductVariantWarehouse.physical_qty is set to the variant stock_qty."""
        self._run_import()
        variant = ProductVariant.objects.get(sku_variant_code="SEG-001-12-BLK")
        warehouse = Warehouse.objects.get(name="Master Warehouse", company=self.company)
        pvw = ProductVariantWarehouse.objects.get(product_variant=variant, warehouse=warehouse)
        self.assertEqual(pvw.physical_qty, 10)
        self.assertEqual(pvw.incoming_qty, 10)


class TestCatalogImportServiceIdempotency(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.service = CatalogImportService()
        self.master_rows = [MasterSkuRow("SEG-001", "SEG", "Setelan Anak", "Supplier A")]
        self.variant_rows = [
            VariantRow(
                sku_code="SEG-001",
                sku_variant_code="SEG-001-12-BLK",
                color_code="blk",
                product_name="Setelan Anak",
                supplier_name="Supplier A",
                color_display="Black",
                size_code="12",
                cogs=Decimal("50000"),
                base_price=Decimal("100000"),
                stock_qty=10,
            )
        ]

    def test_running_import_twice_does_not_create_duplicates(self):
        """Running the import twice does not duplicate products, variants, or warehouse rows."""
        self.service.import_master_data(
            company=self.company,
            master_sku_rows=self.master_rows,
            variant_rows=self.variant_rows,
        )
        self.service.import_master_data(
            company=self.company,
            master_sku_rows=self.master_rows,
            variant_rows=self.variant_rows,
        )
        self.assertEqual(Product.objects.filter(company=self.company).count(), 1)
        self.assertEqual(ProductVariant.objects.filter(company=self.company).count(), 1)
        self.assertEqual(
            ProductVariantWarehouse.objects.filter(product_variant__company=self.company).count(),
            1,
        )
        self.assertEqual(Supplier.objects.filter(company=self.company).count(), 1)
        self.assertEqual(ProductSupplier.objects.filter(company=self.company).count(), 1)


class TestCatalogImportServiceEdgeCases(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.service = CatalogImportService()

    def test_import_skips_product_rows_with_unknown_category(self):
        """Products whose category_code is not in CATEGORY_META are silently skipped."""
        master_rows = [MasterSkuRow("XYZ-001", "XYZ", "Unknown Cat Product", "Sup")]
        variant_rows: list[VariantRow] = []
        result = self.service.import_master_data(
            company=self.company,
            master_sku_rows=master_rows,
            variant_rows=variant_rows,
        )
        self.assertEqual(result.products_created, 0)
        self.assertFalse(Product.objects.filter(sku_code="XYZ-001").exists())

    def test_import_skips_variant_rows_with_no_matching_product(self):
        """Variant rows whose sku_code has no matching product are silently skipped."""
        master_rows: list[MasterSkuRow] = []
        variant_rows = [
            VariantRow(
                sku_code="GHOST-001",
                sku_variant_code="GHOST-001-12-BLK",
                color_code="blk",
                product_name="Ghost",
                supplier_name="",
                color_display="Black",
                size_code="12",
                cogs=Decimal("0"),
                base_price=Decimal("0"),
                stock_qty=0,
            )
        ]
        result = self.service.import_master_data(
            company=self.company,
            master_sku_rows=master_rows,
            variant_rows=variant_rows,
        )
        self.assertEqual(result.variants_created, 0)

    def test_import_handles_product_with_no_supplier(self):
        """Products without a supplier_name import without creating a ProductSupplier."""
        master_rows = [MasterSkuRow("SEG-002", "SEG", "No Sup Product", "")]
        result = self.service.import_master_data(
            company=self.company,
            master_sku_rows=master_rows,
            variant_rows=[],
        )
        self.assertEqual(result.products_created, 1)
        self.assertEqual(result.product_suppliers_created, 0)

    def test_import_multiple_variants_same_product(self):
        """Multiple variants for the same product are all created."""
        master_rows = [MasterSkuRow("SEG-003", "SEG", "Multi Variant Product", "Sup")]
        variant_rows = [
            VariantRow(
                sku_code="SEG-003",
                sku_variant_code=f"SEG-003-{size}-BLK",
                color_code="blk",
                product_name="Multi Variant Product",
                supplier_name="Sup",
                color_display="Black",
                size_code=size,
                cogs=Decimal("50000"),
                base_price=Decimal("100000"),
                stock_qty=5,
            )
            for size in ["10", "12", "14"]
        ]
        result = self.service.import_master_data(
            company=self.company,
            master_sku_rows=master_rows,
            variant_rows=variant_rows,
        )
        self.assertEqual(result.variants_created, 3)
        self.assertEqual(result.warehouse_stock_created, 3)

    def test_import_decimal_precision_preserved_in_cogs(self):
        """Decimal cogs value is faithfully converted to integer for current_cogs."""
        master_rows = [MasterSkuRow("DRS-001", "DRS", "Dress Product", "Sup")]
        variant_rows = [
            VariantRow(
                sku_code="DRS-001",
                sku_variant_code="DRS-001-S-RED",
                color_code="red",
                product_name="Dress Product",
                supplier_name="Sup",
                color_display="Red",
                size_code="S",
                cogs=Decimal("75000"),
                base_price=Decimal("150000"),
                stock_qty=3,
            )
        ]
        self.service.import_master_data(
            company=self.company,
            master_sku_rows=master_rows,
            variant_rows=variant_rows,
        )
        variant = ProductVariant.objects.get(sku_variant_code="DRS-001-S-RED")
        self.assertIsInstance(variant.current_cogs, int)
        self.assertEqual(variant.current_cogs, 75000)
        self.assertIsInstance(variant.base_price, int)
        self.assertEqual(variant.base_price, 150000)

    def test_import_uses_existing_warehouse_if_present(self):
        """If a warehouse already exists, it is reused rather than duplicated."""
        Warehouse.objects.create(
            name="Master Warehouse",
            company=self.company,
            is_active=True,
        )
        master_rows = [MasterSkuRow("PNG-001", "PNG", "Pants", "Sup")]
        self.service.import_master_data(
            company=self.company,
            master_sku_rows=master_rows,
            variant_rows=[],
        )
        self.assertEqual(
            Warehouse.objects.filter(name="Master Warehouse", company=self.company).count(), 1
        )

    def test_import_all_four_known_categories(self):
        """SEG, DRS, JNG, PNG all map to the correct category names."""
        master_rows = [
            MasterSkuRow("SEG-010", "SEG", "Setelan", "Sup"),
            MasterSkuRow("DRS-010", "DRS", "Dress", "Sup"),
            MasterSkuRow("JNG-010", "JNG", "Jeans", "Sup"),
            MasterSkuRow("PNG-010", "PNG", "Pants", "Sup"),
        ]
        self.service.import_master_data(
            company=self.company,
            master_sku_rows=master_rows,
            variant_rows=[],
        )
        self.assertEqual(Category.objects.get(category_code="SEG").name, "Set / Setelan")
        self.assertEqual(Category.objects.get(category_code="DRS").name, "Dress")
        self.assertEqual(Category.objects.get(category_code="JNG").name, "Jeans / Pants")
        self.assertEqual(Category.objects.get(category_code="PNG").name, "Pants")
