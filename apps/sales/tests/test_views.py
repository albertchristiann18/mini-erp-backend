import datetime as dt

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.catalog.tests.factories import ProductFactory, ProductVariantFactory
from apps.inventory.tests.factories import ProductCogsFactory, ProductVariantWarehouseFactory
from apps.sales.models import SalesOrder, SalesOrderItem
from apps.sales.tests.factories import SalesOrderFactory, SalesOrderItemFactory, SalesReturnFactory
from core.factories import CompanyFactory, WarehouseFactory


class SalesOrderAPITest(APITestCase):
    def setUp(self):
        from core.models import UserProfile

        self.client = APIClient()
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.product = ProductFactory(company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product, company=self.company)
        self.pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=100,
        )
        # Create COGS layer for confirm tests
        self.cogs = ProductCogsFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            original_qty=100,
            remaining_qty=100,
            cogs_amount=50000,
        )
        self.user = User.objects.create_user(
            username="sales_api_user", password="password", is_staff=True
        )
        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_create_sales_order(self):
        payload = {
            "warehouse_id": str(self.warehouse.id),
            "company_id": str(self.company.id),
            "order_date": timezone.now().isoformat(),
            "customer_name": "Test Customer",
            "items": [
                {
                    "product_variant_id": str(self.product_variant.id),
                    "quantity": 2,
                    "selling_price": 100000,
                    "discount_amount": 0,
                    "commission_fee": 5000,
                    "service_fee": 3000,
                }
            ],
        }
        response = self.client.post("/sales-orders/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(SalesOrder.objects.count(), 1)
        self.assertEqual(SalesOrderItem.objects.count(), 1)

    def test_list_sales_orders(self):
        SalesOrderFactory(warehouse=self.warehouse, company=self.company)
        SalesOrderFactory(warehouse=self.warehouse, company=self.company)
        response = self.client.get("/sales-orders/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data["results"]), 2)

    def test_get_single_sales_order(self):
        so = SalesOrderFactory(warehouse=self.warehouse, company=self.company)
        SalesOrderItemFactory(
            sales_order=so,
            product_variant=self.product_variant,
            company=self.company,
        )
        response = self.client.get(f"/sales-orders/{so.id}/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["items"]), 1)

    def test_confirm_order_deducts_stock(self):
        so = SalesOrderFactory(warehouse=self.warehouse, company=self.company)
        SalesOrderItemFactory(
            sales_order=so,
            product_variant=self.product_variant,
            company=self.company,
            quantity=5,
            selling_price=100000,
        )
        response = self.client.post(f"/sales-orders/{so.id}/confirm/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 95)

    def test_cancel_pending_order(self):
        so = SalesOrderFactory(warehouse=self.warehouse, company=self.company)
        SalesOrderItemFactory(
            sales_order=so,
            product_variant=self.product_variant,
            company=self.company,
            quantity=5,
        )
        response = self.client.post(f"/sales-orders/{so.id}/cancel/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 100)  # No change

    def test_cancel_confirmed_order_restores_stock(self):
        so = SalesOrderFactory(warehouse=self.warehouse, company=self.company)
        SalesOrderItemFactory(
            sales_order=so,
            product_variant=self.product_variant,
            company=self.company,
            quantity=5,
            selling_price=100000,
        )
        # First confirm
        self.client.post(f"/sales-orders/{so.id}/confirm/", format="json")
        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 95)

        # Then cancel
        so.refresh_from_db()
        response = self.client.post(f"/sales-orders/{so.id}/cancel/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 100)


class CompanyScopedViewsTest(APITestCase):
    """Tests for company-scoped data isolation in sales views."""

    def setUp(self):
        self.company_a = CompanyFactory()
        self.company_b = CompanyFactory()
        self.user_a = User.objects.create_user(
            username="sales_user_a", password="password", is_staff=True
        )
        self.user_b = User.objects.create_user(
            username="sales_user_b", password="password", is_staff=True
        )
        from core.models import UserProfile

        UserProfile.objects.create(user=self.user_a, company=self.company_a, role="admin")
        UserProfile.objects.create(user=self.user_b, company=self.company_b, role="admin")

        self.warehouse_a = WarehouseFactory(company=self.company_a)
        self.warehouse_b = WarehouseFactory(company=self.company_b)

        self.so_a = SalesOrderFactory(warehouse=self.warehouse_a, company=self.company_a)
        self.so_b = SalesOrderFactory(warehouse=self.warehouse_b, company=self.company_b)

        self.ret_a = SalesReturnFactory(sales_order=self.so_a, company=self.company_a)
        self.ret_b = SalesReturnFactory(sales_order=self.so_b, company=self.company_b)

    def test_sales_order_list_scoped_by_company(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get("/sales-orders/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [so["id"] for so in response.data["results"]]
        self.assertIn(str(self.so_a.id), ids)
        self.assertNotIn(str(self.so_b.id), ids)

    def test_sales_return_list_scoped_by_company(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get("/sales-returns/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r["id"] for r in response.data["results"]]
        self.assertIn(str(self.ret_a.id), ids)
        self.assertNotIn(str(self.ret_b.id), ids)

    def test_create_return_rejects_cross_company_sales_order(self):
        self.client.force_authenticate(user=self.user_a)
        payload = {
            "sales_order_id": str(self.so_b.id),
            "reason": "Test cross-company",
            "items": [{"sales_order_item_id": "fake-item-id", "quantity": 1}],
        }
        response = self.client.post("/sales-returns/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class SalesOrderDateFilterTest(APITestCase):
    def setUp(self):
        from core.models import UserProfile

        self.client = APIClient()
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        user = User.objects.create_user(username="staff", password="password", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)
        self.early_order = SalesOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            order_date=timezone.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        )
        self.late_order = SalesOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            order_date=timezone.datetime(2026, 6, 1, tzinfo=dt.timezone.utc),
        )

    def test_date_from_filter(self):
        response = self.client.get("/sales-orders/?date_from=2026-03-01", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [so["id"] for so in response.data["results"]]
        self.assertNotIn(str(self.early_order.id), ids)
        self.assertIn(str(self.late_order.id), ids)

    def test_date_to_filter(self):
        response = self.client.get("/sales-orders/?date_to=2026-03-01", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [so["id"] for so in response.data["results"]]
        self.assertIn(str(self.early_order.id), ids)
        self.assertNotIn(str(self.late_order.id), ids)
