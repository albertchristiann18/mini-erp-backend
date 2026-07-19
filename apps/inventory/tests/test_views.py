import io
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated as IsAuthenticatedPermission
from rest_framework.test import APIClient, APITestCase

_real_auth_has_permission = IsAuthenticatedPermission.has_permission

from apps.catalog.tests.factories import (
    CategoryFactory,
    ProductDimensionImageFactory,
    ProductFactory,
    ProductPhotoFactory,
    ProductVariantFactory,
)
from apps.inventory.models import (
    ProductVariantWarehouse,
    StockMovement,
)
from apps.inventory.services.inventory_service import InventoryService
from apps.inventory.tests.factories import (
    ProductVariantWarehouseFactory,
    StockMovementFactory,
)
from apps.purchasing.factories import PurchaseOrderFactory
from core.factories import CompanyFactory, WarehouseFactory
from core.permissions import IsStaffOrReadOnly as StaffPerm

_real_staff_perm = StaffPerm.has_permission


class CompanyScopedViewsTest(APITestCase):
    """Tests for company-scoped data isolation in inventory views."""

    def setUp(self):
        self.company_a = CompanyFactory()
        self.company_b = CompanyFactory()
        self.user_a = User.objects.create_user(
            username="user_a", password="password", is_staff=True
        )
        self.user_b = User.objects.create_user(
            username="user_b", password="password", is_staff=True
        )
        from core.models import UserProfile

        UserProfile.objects.create(user=self.user_a, company=self.company_a, role="admin")
        UserProfile.objects.create(user=self.user_b, company=self.company_b, role="admin")

        self.warehouse_a = WarehouseFactory(company=self.company_a, is_active=True)
        self.warehouse_b = WarehouseFactory(company=self.company_b, is_active=True)

        self.category_a = CategoryFactory(company=self.company_a)
        self.category_b = CategoryFactory(company=self.company_b)

        self.product_a = ProductFactory(
            company=self.company_a, category=self.category_a, is_active=True
        )
        self.product_b = ProductFactory(
            company=self.company_b, category=self.category_b, is_active=True
        )

        self.variant_a = ProductVariantFactory(
            product=self.product_a, company=self.company_a, is_active=True
        )
        self.variant_b = ProductVariantFactory(
            product=self.product_b, company=self.company_b, is_active=True
        )

    def test_warehouse_list_scoped_by_company(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get("/warehouse/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [w["id"] for w in response.data["results"]]
        self.assertIn(str(self.warehouse_a.id), ids)
        self.assertNotIn(str(self.warehouse_b.id), ids)


class AvgSalesViewTest(APITestCase):
    """Tests for GET /avg-sales/ endpoint"""

    def setUp(self):
        self.client = APIClient()
        user = User.objects.create_user(username="staff", password="password", is_staff=True)
        self.client.force_authenticate(user=user)
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)

    def test_missing_variant_ids_returns_400(self):
        response = self.client.get("/avg-sales/?days=30")
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.data)

    def test_invalid_days_returns_400(self):
        response = self.client.get("/avg-sales/?days=14&variant_ids=abc")
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.data)

    def test_valid_request_no_sales(self):
        variant = ProductVariantFactory(company=self.company)
        response = self.client.get(f"/avg-sales/?days=30&variant_ids={variant.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["avg_sales_per_day"], 0.0)
        self.assertEqual(response.data["results"][0]["total_qty_sold"], 0)

    def test_valid_request_with_sales(self):
        from apps.sales.factories import SalesOrderFactory, SalesOrderItemFactory
        from apps.sales.models import SalesOrder

        variant = ProductVariantFactory(company=self.company)
        so = SalesOrderFactory(
            company=self.company,
            warehouse=self.warehouse,
            status=SalesOrder.OrderStatus.COMPLETED,
            order_date=timezone.now(),
        )
        SalesOrderItemFactory(
            sales_order=so,
            product_variant=variant,
            quantity=30,
        )
        response = self.client.get(f"/avg-sales/?days=30&variant_ids={variant.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["avg_sales_per_day"], 1.0)
        self.assertEqual(response.data["results"][0]["total_qty_sold"], 30)

    def test_cancelled_orders_excluded(self):
        from apps.sales.factories import SalesOrderFactory, SalesOrderItemFactory
        from apps.sales.models import SalesOrder

        variant = ProductVariantFactory(company=self.company)
        so = SalesOrderFactory(
            company=self.company,
            warehouse=self.warehouse,
            status=SalesOrder.OrderStatus.CANCELLED,
            order_date=timezone.now(),
        )
        SalesOrderItemFactory(
            sales_order=so,
            product_variant=variant,
            quantity=30,
        )
        response = self.client.get(f"/avg-sales/?days=30&variant_ids={variant.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["avg_sales_per_day"], 0.0)

    def test_7_day_window(self):
        from apps.sales.factories import SalesOrderFactory, SalesOrderItemFactory
        from apps.sales.models import SalesOrder

        variant = ProductVariantFactory(company=self.company)
        so = SalesOrderFactory(
            company=self.company,
            warehouse=self.warehouse,
            status=SalesOrder.OrderStatus.COMPLETED,
            order_date=timezone.now(),
        )
        SalesOrderItemFactory(
            sales_order=so,
            product_variant=variant,
            quantity=7,
        )
        response = self.client.get(f"/avg-sales/?days=7&variant_ids={variant.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["avg_sales_per_day"], 1.0)


class InventorySummaryAPITest(APITestCase):
    """Tests for GET /api/inventory/inventory-summary/ endpoint"""

    def setUp(self):
        self.client = APIClient()
        self.company = CompanyFactory()
        self.other_company = CompanyFactory()
        self.user = User.objects.create_user(
            username="inventory_user", password="password", is_staff=True
        )
        from core.models import UserProfile

        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

        self.warehouse = WarehouseFactory(company=self.company, is_active=True)
        self.other_warehouse = WarehouseFactory(company=self.other_company, is_active=True)

    def test_returns_products_scoped_to_company(self):
        """Company A sees only its own products."""
        category_a = CategoryFactory(company=self.company)
        product_a = ProductFactory(company=self.company, category=category_a, is_active=True)
        variant_a1 = ProductVariantFactory(
            product=product_a,
            company=self.company,
            is_active=True,
            current_cogs=50000,
            base_price=100000,
            variant_values={"1": "Navy"},
        )
        variant_a2 = ProductVariantFactory(
            product=product_a,
            company=self.company,
            is_active=True,
            current_cogs=60000,
            base_price=120000,
            variant_values={"1": "Red"},
        )
        ProductVariantWarehouseFactory(
            product_variant=variant_a1,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=10,
        )
        ProductVariantWarehouseFactory(
            product_variant=variant_a2,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=5,
        )

        category_b = CategoryFactory(company=self.other_company)
        product_b = ProductFactory(company=self.other_company, category=category_b, is_active=True)
        variant_b = ProductVariantFactory(
            product=product_b,
            company=self.other_company,
            is_active=True,
            variant_values={"1": "Blue"},
        )
        ProductVariantWarehouseFactory(
            product_variant=variant_b,
            warehouse=self.other_warehouse,
            company=self.other_company,
            physical_qty=3,
        )

        response = self.client.get("/inventory-summary/")
        self.assertEqual(response.status_code, 200)

        product_ids = [p["product_id"] for p in response.data["products"]]
        self.assertIn(str(product_a.id), product_ids)
        self.assertNotIn(str(product_b.id), product_ids)

        self.assertIn("warehouses", response.data)
        self.assertIn("products", response.data)
        self.assertIn("summary", response.data)

    def test_variant_includes_warehouse_stocks(self):
        """Variant response includes warehouse_stocks dict and total_qty."""
        category = CategoryFactory(company=self.company)
        product = ProductFactory(company=self.company, category=category, is_active=True)
        variant = ProductVariantFactory(
            product=product,
            company=self.company,
            is_active=True,
            current_cogs=50000,
            base_price=100000,
        )
        warehouse2 = WarehouseFactory(company=self.company, is_active=True)

        ProductVariantWarehouseFactory(
            product_variant=variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=7,
        )
        ProductVariantWarehouseFactory(
            product_variant=variant,
            warehouse=warehouse2,
            company=self.company,
            physical_qty=3,
        )

        response = self.client.get("/inventory-summary/")
        self.assertEqual(response.status_code, 200)

        variant_data = response.data["products"][0]["variants"][0]
        self.assertEqual(variant_data["total_qty"], 10)
        self.assertIn(str(self.warehouse.id), variant_data["warehouse_stocks"])
        self.assertIn(str(warehouse2.id), variant_data["warehouse_stocks"])
        self.assertEqual(variant_data["warehouse_stocks"][str(self.warehouse.id)], 7)
        self.assertEqual(variant_data["warehouse_stocks"][str(warehouse2.id)], 3)

    def test_summary_totals(self):
        """Summary totals are computed correctly from current_cogs * total_qty."""
        category = CategoryFactory(company=self.company)
        product = ProductFactory(company=self.company, category=category, is_active=True)
        variant = ProductVariantFactory(
            product=product,
            company=self.company,
            is_active=True,
            current_cogs=100000,
            base_price=200000,
        )
        ProductVariantWarehouseFactory(
            product_variant=variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=5,
        )

        response = self.client.get("/inventory-summary/")
        self.assertEqual(response.status_code, 200)

        summary = response.data["summary"]
        self.assertEqual(summary["total_cogs_stock"], 500000)
        self.assertEqual(summary["total_selling_price"], 1000000)

    def test_unauthenticated_returns_403(self):
        """Unauthenticated request returns 403."""
        with patch.object(IsAuthenticatedPermission, "has_permission", _real_auth_has_permission):
            client = APIClient()
            response = client.get("/inventory-summary/")
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_photo_url_is_none_when_no_photo(self):
        """Product without photo returns null photo_url."""
        category = CategoryFactory(company=self.company)
        product = ProductFactory(
            company=self.company,
            category=category,
            is_active=True,
            product_photo=None,
        )
        ProductVariantFactory(
            product=product,
            company=self.company,
            is_active=True,
            current_cogs=10000,
            base_price=20000,
        )

        response = self.client.get("/inventory-summary/")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["products"][0]["photo_url"])

    def test_photo_url_uses_primary_photo_from_gallery(self):
        """Photo URL comes from the primary ProductPhoto gallery, not legacy product_photo."""
        category = CategoryFactory(company=self.company)
        product = ProductFactory(
            company=self.company,
            category=category,
            is_active=True,
            product_photo=None,
        )
        ProductVariantFactory(
            product=product,
            company=self.company,
            is_active=True,
            current_cogs=10000,
            base_price=20000,
        )
        ProductPhotoFactory(product=product, company=self.company, is_primary=True, order=0)

        response = self.client.get("/inventory-summary/")
        self.assertEqual(response.status_code, 200)
        photo_url = response.data["products"][0]["photo_url"]
        self.assertIsNotNone(photo_url)
        self.assertIn("test_photo", photo_url)

    def test_no_n1_queries(self):
        """Number of queries is bounded (no N+1)."""
        category = CategoryFactory(company=self.company)
        warehouse2 = WarehouseFactory(company=self.company, is_active=True)
        products = ProductFactory.create_batch(
            3, company=self.company, category=category, is_active=True
        )
        value_keys = ["1", "2"]
        for p_idx, product in enumerate(products):
            for v_idx in range(2):
                variant = ProductVariantFactory(
                    product=product,
                    company=self.company,
                    is_active=True,
                    current_cogs=10000,
                    base_price=20000,
                    variant_values={value_keys[v_idx]: f"VAL{p_idx}{v_idx}"},
                )
                ProductVariantWarehouseFactory(
                    product_variant=variant,
                    warehouse=self.warehouse,
                    company=self.company,
                    physical_qty=5,
                )
                ProductVariantWarehouseFactory(
                    product_variant=variant,
                    warehouse=warehouse2,
                    company=self.company,
                    physical_qty=5,
                )

        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get("/inventory-summary/")
            self.assertEqual(response.status_code, 200)
            self.assertLessEqual(len(ctx), 8)


class StockMovementAPITest(APITestCase):
    def setUp(self):
        from core.models import UserProfile

        self.user = User.objects.create_user(
            username="stockuser", password="password", is_staff=True
        )
        self.client.force_authenticate(user=self.user)
        self.company = CompanyFactory()
        UserProfile.objects.create(user=self.user, company=self.company)
        self.warehouse_a = WarehouseFactory(company=self.company)
        self.warehouse_b = WarehouseFactory(company=self.company)
        self.variant = ProductVariantFactory(company=self.company)

    def test_list_stock_movements(self):
        StockMovementFactory.create_batch(
            3,
            company=self.company,
            product_variant=self.variant,
            warehouse=self.warehouse_a,
        )
        response = self.client.get("/stock-movements/")
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data["results"]), 3)

    def test_movement_type_returned_as_full_name(self):
        StockMovementFactory(
            company=self.company,
            product_variant=self.variant,
            warehouse=self.warehouse_a,
            movement_type=StockMovement.MovementType.INBOUND,
        )
        response = self.client.get("/stock-movements/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["movement_type"], "INBOUND")

    def test_filter_by_warehouse(self):
        StockMovementFactory(
            company=self.company,
            product_variant=self.variant,
            warehouse=self.warehouse_a,
        )
        StockMovementFactory(
            company=self.company,
            product_variant=self.variant,
            warehouse=self.warehouse_b,
        )
        response = self.client.get(f"/stock-movements/?warehouse={self.warehouse_a.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)

    def test_filter_by_cdate_after(self):
        StockMovementFactory(
            company=self.company,
            product_variant=self.variant,
            warehouse=self.warehouse_a,
        )
        from datetime import timedelta

        tomorrow = (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        response = self.client.get(f"/stock-movements/?cdate_after={tomorrow}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 0)

    def test_unauthenticated_denied(self):
        with patch.object(IsAuthenticatedPermission, "has_permission", _real_auth_has_permission):
            client = APIClient()
            response = client.get(reverse("stock-movement-list"))
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_filter_by_product_variant(self):
        other_variant = ProductVariantFactory(company=self.company)
        StockMovementFactory(
            company=self.company,
            product_variant=self.variant,
            warehouse=self.warehouse_a,
        )
        StockMovementFactory(
            company=self.company,
            product_variant=other_variant,
            warehouse=self.warehouse_a,
        )
        response = self.client.get(
            reverse("stock-movement-list"),
            {"product_variant": self.variant.id},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["product_variant"], str(self.variant.id))

    def test_filter_by_movement_type(self):
        StockMovementFactory(
            company=self.company,
            product_variant=self.variant,
            warehouse=self.warehouse_a,
            movement_type=StockMovement.MovementType.PURCHASE,
        )
        StockMovementFactory(
            company=self.company,
            product_variant=self.variant,
            warehouse=self.warehouse_a,
            movement_type=StockMovement.MovementType.INBOUND,
        )

        # Filter by full name lookup
        response = self.client.get(
            reverse("stock-movement-list"),
            {"movement_type": "PURCHASE"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)

        # Filter by short code directly
        response = self.client.get(
            reverse("stock-movement-list"),
            {"movement_type": "PUR"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)

    def test_filter_by_cdate_before(self):
        StockMovementFactory(
            company=self.company,
            product_variant=self.variant,
            warehouse=self.warehouse_a,
        )
        from datetime import timedelta

        tomorrow = (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        response = self.client.get(
            reverse("stock-movement-list"),
            {"cdate_before": tomorrow},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data["results"]), 1)


class MarketplaceReconcileTest(APITestCase):
    """Tests for marketplace stock reconciliation from xlsx file upload."""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.variant = ProductVariantFactory(
            product=self.product,
            company=self.company,
            sku_variant_code="SKU-TEST-001",
            is_active=True,
        )
        self.service = InventoryService()
        self.user = User.objects.create_user(
            username="reconcile_user", password="password", is_staff=True
        )
        from core.models import UserProfile

        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    @staticmethod
    def _make_xlsx(headers: list, data_rows: list[list]) -> io.BytesIO:
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(headers)
        for row in data_rows:
            ws.append(row)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf

    # --- Parser tests ---

    def test_parse_marketplace_xlsx_shopee_format(self):
        """Row 1 = metadata, row 2 = headers, row 3+ = data."""
        rows = [
            ["Shopee Export Report", None, None],
            ["SKU Reference No.", "Current Stock", "Product Name"],
            ["SKU-001", "50", "Product A"],
            ["SKU-002", "30", "Product B"],
        ]
        buf = self._make_xlsx(rows[0], rows[1:])
        # first row is metadata with no matching headers, so header is row 2
        result = InventoryService.parse_marketplace_xlsx(buf)
        self.assertEqual(len(result), 2)
        self.assertEqual(
            result[0],
            {
                "sku": "SKU-001",
                "qty": 50,
                "file_product_name": "Product A",
                "file_variant_name": "",
            },
        )
        self.assertEqual(
            result[1],
            {
                "sku": "SKU-002",
                "qty": 30,
                "file_product_name": "Product B",
                "file_variant_name": "",
            },
        )

    def test_parse_marketplace_xlsx_simple_format(self):
        """Headers in row 1 = ["SKU", "Stock"]."""
        buf = self._make_xlsx(
            ["SKU", "Stock"],
            [["VAR-A", "10"], ["VAR-B", "20"]],
        )
        result = InventoryService.parse_marketplace_xlsx(buf)
        self.assertEqual(len(result), 2)
        self.assertEqual(
            result[0], {"sku": "VAR-A", "qty": 10, "file_product_name": "", "file_variant_name": ""}
        )
        self.assertEqual(
            result[1], {"sku": "VAR-B", "qty": 20, "file_product_name": "", "file_variant_name": ""}
        )

    def test_parse_marketplace_xlsx_no_valid_columns(self):
        """Unrecognized headers return []."""
        buf = self._make_xlsx(
            ["Product", "Price"],
            [["A", "100"], ["B", "200"]],
        )
        result = InventoryService.parse_marketplace_xlsx(buf)
        self.assertEqual(result, [])

    def test_parse_marketplace_xlsx_skips_empty_sku(self):
        """Rows with empty SKU are skipped."""
        buf = self._make_xlsx(
            ["SKU", "Stock"],
            [["VAR-A", "10"], ["", "20"], [None, "30"]],
        )
        result = InventoryService.parse_marketplace_xlsx(buf)
        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0], {"sku": "VAR-A", "qty": 10, "file_product_name": "", "file_variant_name": ""}
        )

    def test_parse_marketplace_xlsx_handles_float_qty(self):
        """Stock values that are floats are converted to int."""
        buf = self._make_xlsx(
            ["SKU", "Stock"],
            [["VAR-A", 10.0], ["VAR-B", 20.7]],
        )
        result = InventoryService.parse_marketplace_xlsx(buf)
        self.assertEqual(result[0]["qty"], 10)
        self.assertEqual(result[1]["qty"], 20)

    def test_parse_marketplace_xlsx_invalid_activepane(self):
        """Shopee xlsx with invalid activePane attribute is handled gracefully."""
        import zipfile

        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["SKU", "Stock"])
        ws.append(["SKU-001", "50"])
        ws.append(["SKU-002", "30"])

        buf = io.BytesIO()
        wb.save(buf)
        raw = buf.getvalue()

        corrupted = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw), "r") as zin, zipfile.ZipFile(corrupted, "w") as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename.startswith("xl/worksheets/") and info.filename.endswith(".xml"):
                    data = data.replace(
                        b"</worksheet>",
                        b' activePane="invalid" rest of the junk</worksheet>',
                        1,
                    )
                zout.writestr(info, data)
        corrupted.seek(0)

        result = InventoryService.parse_marketplace_xlsx(corrupted)
        self.assertEqual(len(result), 2)
        self.assertEqual(
            result[0],
            {"sku": "SKU-001", "qty": 50, "file_product_name": "", "file_variant_name": ""},
        )
        self.assertEqual(
            result[1],
            {"sku": "SKU-002", "qty": 30, "file_product_name": "", "file_variant_name": ""},
        )

    def test_parse_marketplace_xlsx_skips_non_numeric_qty(self):
        """Rows with a non-numeric stock cell are skipped gracefully."""
        buf = self._make_xlsx(
            ["SKU", "Stock"],
            [["VAR-A", "abc"], ["VAR-B", "10"]],
        )
        result = InventoryService.parse_marketplace_xlsx(buf)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["qty"], 10)

    # --- Service tests ---

    def test_reconcile_same_stock_skipped(self):
        """Variant where stock matches current is skipped, no StockMovement."""
        ProductVariantWarehouseFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=50,
        )
        rows = [{"sku": "SKU-TEST-001", "qty": 50}]
        result = self.service.reconcile_marketplace_stock(
            rows=rows,
            warehouse_id=str(self.warehouse.id),
            company_id=str(self.company.id),
            marketplace_name="Shopee",
        )
        self.assertEqual(result["summary"]["skipped"], 1)
        self.assertEqual(result["summary"]["reconciled"], 0)
        self.assertEqual(StockMovement.objects.count(), 0)
        self.assertEqual(result["skipped"][0]["product_name"], "Test Product")
        self.assertEqual(result["skipped"][0]["variant_name"], "Test Product Variant")

    def test_reconcile_different_stock_creates_movement(self):
        """Variant where stock differs creates a MARKETPLACE_SYNC movement."""
        ProductVariantWarehouseFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=50,
        )
        rows = [{"sku": "SKU-TEST-001", "qty": 80}]
        result = self.service.reconcile_marketplace_stock(
            rows=rows,
            warehouse_id=str(self.warehouse.id),
            company_id=str(self.company.id),
            marketplace_name="Shopee",
        )
        self.assertEqual(result["summary"]["reconciled"], 1)
        self.assertEqual(result["summary"]["skipped"], 0)
        movement = StockMovement.objects.last()
        self.assertIsNotNone(movement)
        self.assertEqual(movement.movement_type, StockMovement.MovementType.MARKETPLACE_SYNC)
        self.assertEqual(movement.balance_before, 50)
        self.assertEqual(movement.balance_after, 80)
        self.assertEqual(movement.quantity, 30)
        self.assertEqual(result["reconciled"][0]["product_name"], "Test Product")
        self.assertEqual(result["reconciled"][0]["variant_name"], "Test Product Variant")

    def test_reconcile_not_found_sku(self):
        """SKU not matching any variant appears in not_found."""
        rows = [{"sku": "SKU-NONEXIST", "qty": 10}]
        result = self.service.reconcile_marketplace_stock(
            rows=rows,
            warehouse_id=str(self.warehouse.id),
            company_id=str(self.company.id),
            marketplace_name="Shopee",
        )
        self.assertEqual(result["summary"]["not_found"], 1)
        self.assertEqual(
            result["not_found"],
            [{"sku": "SKU-NONEXIST", "file_product_name": "", "file_variant_name": ""}],
        )

    def test_reconcile_dry_run(self):
        """dry_run=True returns reconciled list but creates no StockMovement."""
        ProductVariantWarehouseFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=50,
        )
        rows = [{"sku": "SKU-TEST-001", "qty": 100}]
        result = self.service.reconcile_marketplace_stock(
            rows=rows,
            warehouse_id=str(self.warehouse.id),
            company_id=str(self.company.id),
            marketplace_name="Shopee",
            dry_run=True,
        )
        self.assertEqual(result["summary"]["reconciled"], 1)
        self.assertTrue(result["dry_run"])
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_reconcile_creates_new_pvw(self):
        """Variant with no PVW gets one created during reconciliation."""
        rows = [{"sku": "SKU-TEST-001", "qty": 25}]
        result = self.service.reconcile_marketplace_stock(
            rows=rows,
            warehouse_id=str(self.warehouse.id),
            company_id=str(self.company.id),
            marketplace_name="Tokopedia",
        )
        self.assertEqual(result["summary"]["reconciled"], 1)
        pvw = ProductVariantWarehouse.objects.get(
            product_variant=self.variant,
            warehouse=self.warehouse,
        )
        self.assertEqual(pvw.physical_qty, 25)

    def test_reconcile_empty_rows(self):
        """Empty rows returns empty summary."""
        result = self.service.reconcile_marketplace_stock(
            rows=[],
            warehouse_id=str(self.warehouse.id),
            company_id=str(self.company.id),
            marketplace_name="Shopee",
        )
        self.assertEqual(result["summary"]["total"], 0)

    # --- Endpoint tests ---

    def test_reconcile_endpoint_no_file(self):
        """POST without file returns 400."""
        response = self.client.post(
            "/inventory/marketplace_reconcile/",
            {"warehouse_id": str(self.warehouse.id)},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.data)

    def test_reconcile_endpoint_no_warehouse(self):
        """POST without warehouse_id returns 400."""
        buf = self._make_xlsx(["SKU", "Stock"], [["A", "10"]])
        response = self.client.post(
            "/inventory/marketplace_reconcile/",
            {"file": buf},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.data)

    def test_reconcile_endpoint_success(self):
        """POST with valid file and warehouse reconciles stock."""
        ProductVariantWarehouseFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=50,
        )
        buf = self._make_xlsx(
            ["SKU", "Stock"],
            [["SKU-TEST-001", "75"]],
        )
        response = self.client.post(
            "/inventory/marketplace_reconcile/",
            {
                "file": buf,
                "warehouse_id": str(self.warehouse.id),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["reconciled"], 1)
        self.assertEqual(response.data["reconciled"][0]["before"], 50)
        self.assertEqual(response.data["reconciled"][0]["after"], 75)

    def test_reconcile_endpoint_dry_run(self):
        """POST with dry_run=true does not create StockMovement."""
        ProductVariantWarehouseFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=50,
        )
        buf = self._make_xlsx(
            ["SKU", "Stock"],
            [["SKU-TEST-001", "100"]],
        )
        response = self.client.post(
            "/inventory/marketplace_reconcile/",
            {
                "file": buf,
                "warehouse_id": str(self.warehouse.id),
                "dry_run": "true",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["dry_run"])
        self.assertEqual(StockMovement.objects.count(), 0)


class QCPPhase7Test(APITestCase):
    """Tests for QCP Phase 7 — variant last price tracking and variant_values."""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.user = User.objects.create_user(
            username="qcp7_inv_user", password="password", is_staff=True
        )
        from core.models import UserProfile

        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_po_detail_serializer_includes_variant_values(self):
        from apps.purchasing.factories import PurchaseOrderDetailFactory

        self.product_variant.variant_values = {"color": "Red", "size": "M"}
        self.product_variant.save()
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
        )
        response = self.client.get(f"/purchase-order/{po.id}/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["order_details"][0]["variant_values"],
            {"color": "Red", "size": "M"},
        )

    def test_supplier_link_populated_on_detail_creation(self):
        from apps.purchasing.factories import (
            ProductSupplierFactory,
            SupplierFactory,
        )

        supplier = SupplierFactory(company=self.company)
        ProductSupplierFactory(
            product=self.product,
            supplier=supplier,
            company=self.company,
            supplier_link="https://supplier.com/item",
        )
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            supplier=supplier,
            status="DRAFT",
        )
        response = self.client.patch(
            f"/purchase-order/{po.id}/",
            {
                "order_details": [
                    {
                        "product_variant_id": str(self.product_variant.id),
                        "ordered_qty": 10,
                        "unit_price_foreign": 15.00,
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        detail_response = self.client.get(f"/purchase-order/{po.id}/", format="json")
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            detail_response.data["order_details"][0]["product_supplier_link"],
            "https://supplier.com/item",
        )

    def test_supplier_link_prefers_po_supplier(self):
        from apps.purchasing.factories import (
            ProductSupplierFactory,
            SupplierFactory,
        )

        supplier_a = SupplierFactory(company=self.company)
        supplier_b = SupplierFactory(company=self.company)
        ProductSupplierFactory(
            product=self.product,
            supplier=supplier_a,
            company=self.company,
            supplier_link="https://po-supplier.com",
        )
        ProductSupplierFactory(
            product=self.product,
            supplier=supplier_b,
            company=self.company,
            supplier_link="https://other.com",
        )
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            supplier=supplier_a,
            status="DRAFT",
        )
        response = self.client.patch(
            f"/purchase-order/{po.id}/",
            {
                "order_details": [
                    {
                        "product_variant_id": str(self.product_variant.id),
                        "ordered_qty": 10,
                        "unit_price_foreign": 15.00,
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        from apps.purchasing.models import PurchaseOrderDetail

        detail = PurchaseOrderDetail.objects.filter(purchase_order=po).first()
        self.assertIsNotNone(detail)
        self.assertEqual(detail.supplier_link, "https://po-supplier.com")
        detail_response = self.client.get(f"/purchase-order/{po.id}/", format="json")
        self.assertEqual(
            detail_response.data["order_details"][0]["product_supplier_link"],
            "https://po-supplier.com",
        )


class PODetailDimensionPhotoTest(APITestCase):
    def setUp(self):
        from core.models import UserProfile

        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(
            category=self.category, company=self.company, dim1_key="Warna"
        )
        self.variant = ProductVariantFactory(
            product=self.product,
            company=self.company,
            variant_values={"Warna": "White"},
        )
        self.user = User.objects.create_user(
            username="po_dim_user", password="password", is_staff=True
        )
        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_po_detail_returns_dimension_image_url_when_variant_has_matching_dim_value(self):
        from apps.purchasing.factories import PurchaseOrderDetailFactory

        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        PurchaseOrderDetailFactory(
            purchase_order=po, product_variant=self.variant, company=self.company
        )
        ProductDimensionImageFactory(product=self.product, dim_key="Warna", dim_value="White")

        response = self.client.get(f"/purchase-order/{po.id}/", format="json")
        self.assertEqual(response.status_code, 200)
        detail_data = response.data["order_details"][0]
        self.assertIn("product_photo_url", detail_data)
        self.assertIsNotNone(detail_data["product_photo_url"])
        self.assertIn("dimension_images", detail_data["product_photo_url"])

    def test_po_detail_falls_back_to_variant_photo_when_no_dimension_image(self):
        from django.core.files.base import ContentFile

        from apps.purchasing.factories import PurchaseOrderDetailFactory

        self.variant.photo.save("variant.jpg", ContentFile(b"variant_img"), save=True)

        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        PurchaseOrderDetailFactory(
            purchase_order=po, product_variant=self.variant, company=self.company
        )

        response = self.client.get(f"/purchase-order/{po.id}/", format="json")
        self.assertEqual(response.status_code, 200)
        detail_data = response.data["order_details"][0]
        self.assertIsNotNone(detail_data["product_photo_url"])
        self.assertIn("variants/photos/", detail_data["product_photo_url"])

    def test_po_detail_returns_product_dim1_key_field(self):
        from apps.purchasing.factories import PurchaseOrderDetailFactory

        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        PurchaseOrderDetailFactory(
            purchase_order=po, product_variant=self.variant, company=self.company
        )

        response = self.client.get(f"/purchase-order/{po.id}/", format="json")
        self.assertEqual(response.status_code, 200)
        detail_data = response.data["order_details"][0]
        self.assertIn("product_dim1_key", detail_data)
        self.assertEqual(detail_data["product_dim1_key"], "Warna")

    def test_po_detail_product_dim1_key_is_none_for_product_without_dim1_key(self):
        from apps.purchasing.factories import PurchaseOrderDetailFactory

        product_no_dim = ProductFactory(category=self.category, company=self.company, dim1_key="")
        variant_no_dim = ProductVariantFactory(
            product=product_no_dim, company=self.company, variant_values={}
        )
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        PurchaseOrderDetailFactory(
            purchase_order=po, product_variant=variant_no_dim, company=self.company
        )

        response = self.client.get(f"/purchase-order/{po.id}/", format="json")
        self.assertEqual(response.status_code, 200)
        detail_data = response.data["order_details"][0]
        self.assertIsNone(detail_data["product_dim1_key"])
