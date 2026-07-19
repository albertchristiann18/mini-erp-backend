import datetime as dt

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.catalog.factories import ProductFactory, ProductVariantFactory
from apps.inventory.factories import ProductCogsFactory, ProductVariantWarehouseFactory
from apps.inventory.models import StockMovement
from apps.sales.factories import SalesOrderFactory, SalesOrderItemFactory, SalesReturnFactory
from apps.sales.models import SalesOrder, SalesOrderCogsDetail, SalesOrderItem, SalesReturn
from apps.sales.services.cogs_consumption import CogsConsumptionService
from apps.sales.services.sales_service import SalesOrderService, SalesReturnService
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


class SalesOrderServiceTest(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.product = ProductFactory(company=self.company)
        self.variant = ProductVariantFactory(
            product=self.product, company=self.company, total_available_qty=100
        )
        self.pvw = ProductVariantWarehouseFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=100,
        )
        self.cogs_layer = ProductCogsFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            original_qty=100,
            remaining_qty=100,
            cogs_amount=50000,
        )
        self.service = SalesOrderService()

    def _create_so_with_items(self, qty=5):
        so = SalesOrderFactory(warehouse=self.warehouse, company=self.company)
        SalesOrderItemFactory(
            sales_order=so,
            product_variant=self.variant,
            company=self.company,
            quantity=qty,
            selling_price=100000,
        )
        return so

    def test_create_sales_order_success(self):
        data = {
            "warehouse_id": str(self.warehouse.id),
            "company_id": str(self.company.id),
            "order_date": timezone.now(),
            "items": [
                {
                    "product_variant_id": str(self.variant.id),
                    "quantity": 3,
                    "selling_price": 100000,
                    "discount_amount": 0,
                    "commission_fee": 0,
                    "service_fee": 0,
                }
            ],
        }
        so = self.service.create_sales_order(data)
        self.assertEqual(so.items.count(), 1)
        self.assertEqual(so.subtotal, 300000)

    def test_confirm_order_deducts_stock_and_creates_movement(self):
        so = self._create_so_with_items(5)
        self.service.confirm_order(so)

        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 95)

        self.variant.refresh_from_db()
        self.assertEqual(self.variant.total_available_qty, 95)

        movements = StockMovement.objects.filter(reference_number=so.order_number)
        self.assertTrue(movements.exists())

    def test_confirm_order_fails_insufficient_stock(self):
        so = self._create_so_with_items(200)
        with self.assertRaises(ValidationError):
            self.service.confirm_order(so)

    def test_confirm_order_consumes_fifo_cogs(self):
        so = self._create_so_with_items(5)
        self.service.confirm_order(so)

        self.cogs_layer.refresh_from_db()
        self.assertEqual(self.cogs_layer.remaining_qty, 95)

        item = so.items.first()
        self.assertEqual(item.actual_cogs_per_unit, 50000)
        self.assertEqual(item.actual_cogs_total, 250000)

    def test_cancel_confirmed_order_reverses_stock_and_cogs(self):
        so = self._create_so_with_items(5)
        self.service.confirm_order(so)
        self.service.cancel_order(so)

        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 100)

        self.cogs_layer.refresh_from_db()
        self.assertEqual(self.cogs_layer.remaining_qty, 100)

        self.variant.refresh_from_db()
        self.assertEqual(self.variant.total_available_qty, 100)

    def test_cancel_pending_order_no_stock_change(self):
        so = self._create_so_with_items(5)
        self.service.cancel_order(so)

        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 100)

    def test_status_transition_invalid_raises_error(self):
        so = self._create_so_with_items(5)
        so.status = SalesOrder.OrderStatus.COMPLETED
        so.save()

        with self.assertRaises(ValidationError):
            self.service.confirm_order(so)

    def test_recalculate_totals_correct(self):
        so = SalesOrderFactory(
            warehouse=self.warehouse, company=self.company, shipping_fee_seller=5000
        )
        SalesOrderItemFactory(
            sales_order=so,
            product_variant=self.variant,
            company=self.company,
            quantity=2,
            selling_price=100000,
            discount_amount=10000,
            commission_fee=5000,
            service_fee=3000,
            total_marketplace_fee=8000,
        )
        self.service._recalculate_totals(so)
        so.refresh_from_db()

        self.assertEqual(so.subtotal, 200000)
        self.assertEqual(so.total_discount, 10000)
        self.assertEqual(so.total_marketplace_fee, 8000)
        self.assertEqual(so.net_revenue, 200000 - 10000 - 8000 - 5000)


class CogsConsumptionServiceTest(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.product = ProductFactory(company=self.company)
        self.variant = ProductVariantFactory(
            product=self.product, company=self.company, total_available_qty=100
        )
        self.service = CogsConsumptionService()

    def _create_cogs_layer(self, qty, cogs_amount, purchase_date="2026-01-01"):
        return ProductCogsFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            original_qty=qty,
            remaining_qty=qty,
            cogs_amount=cogs_amount,
            purchase_date=purchase_date,
        )

    def _create_so_item(self, qty):
        so = SalesOrderFactory(warehouse=self.warehouse, company=self.company)
        return SalesOrderItemFactory(
            sales_order=so,
            product_variant=self.variant,
            company=self.company,
            quantity=qty,
            selling_price=100000,
        )

    def test_consume_fifo_single_layer(self):
        layer = self._create_cogs_layer(50, 50000)
        item = self._create_so_item(10)

        details = self.service.consume_fifo(item, self.warehouse.id)

        self.assertEqual(len(details), 1)
        layer.refresh_from_db()
        self.assertEqual(layer.remaining_qty, 40)
        item.refresh_from_db()
        self.assertEqual(item.actual_cogs_total, 500000)

    def test_consume_fifo_multiple_layers(self):
        layer1 = self._create_cogs_layer(5, 40000, "2026-01-01")
        layer2 = self._create_cogs_layer(10, 60000, "2026-02-01")
        item = self._create_so_item(8)

        details = self.service.consume_fifo(item, self.warehouse.id)

        self.assertEqual(len(details), 2)
        layer1.refresh_from_db()
        layer2.refresh_from_db()
        self.assertEqual(layer1.remaining_qty, 0)
        self.assertEqual(layer2.remaining_qty, 7)

        item.refresh_from_db()
        expected_cogs = (5 * 40000) + (3 * 60000)
        self.assertEqual(item.actual_cogs_total, expected_cogs)

    def test_consume_fifo_insufficient_stock_raises_error(self):
        self._create_cogs_layer(5, 50000)
        item = self._create_so_item(10)

        with self.assertRaises(ValidationError):
            self.service.consume_fifo(item, self.warehouse.id)

    def test_reverse_fifo_restores_cogs_layers(self):
        layer = self._create_cogs_layer(50, 50000)
        item = self._create_so_item(10)

        self.service.consume_fifo(item, self.warehouse.id)
        layer.refresh_from_db()
        self.assertEqual(layer.remaining_qty, 40)

        self.service.reverse_fifo(item)
        layer.refresh_from_db()
        self.assertEqual(layer.remaining_qty, 50)

        item.refresh_from_db()
        self.assertEqual(item.actual_cogs_total, 0)
        self.assertEqual(SalesOrderCogsDetail.objects.filter(sales_order_item=item).count(), 0)

    def test_consume_fifo_creates_cogs_detail_records(self):
        self._create_cogs_layer(50, 50000)
        item = self._create_so_item(10)

        self.service.consume_fifo(item, self.warehouse.id)

        details = SalesOrderCogsDetail.objects.filter(sales_order_item=item)
        self.assertEqual(details.count(), 1)
        detail = details.first()
        self.assertEqual(detail.quantity_consumed, 10)
        self.assertEqual(detail.cogs_per_unit, 50000)
        self.assertEqual(detail.total_cogs, 500000)


class SalesReturnServiceTest(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.product = ProductFactory(company=self.company)
        self.variant = ProductVariantFactory(
            product=self.product, company=self.company, total_available_qty=100
        )
        self.pvw = ProductVariantWarehouseFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=100,
        )
        self.cogs_layer = ProductCogsFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            original_qty=100,
            remaining_qty=100,
            cogs_amount=50000,
        )
        self.so_service = SalesOrderService()
        self.return_service = SalesReturnService()

    def _create_confirmed_so(self, qty=10):
        so = SalesOrderFactory(warehouse=self.warehouse, company=self.company)
        SalesOrderItemFactory(
            sales_order=so,
            product_variant=self.variant,
            company=self.company,
            quantity=qty,
            selling_price=100000,
        )
        self.so_service.confirm_order(so)
        return so

    def test_create_return_success(self):
        so = self._create_confirmed_so(10)
        item = so.items.first()

        data = {
            "reason": "Defective",
            "items": [{"sales_order_item_id": str(item.id), "quantity": 3}],
        }
        ret = self.return_service.create_return(so, data)
        self.assertEqual(ret.items.count(), 1)
        self.assertEqual(ret.items.first().quantity, 3)

    def test_create_return_exceeds_qty_raises_error(self):
        so = self._create_confirmed_so(10)
        item = so.items.first()

        data = {
            "reason": "Defective",
            "items": [{"sales_order_item_id": str(item.id), "quantity": 15}],
        }
        with self.assertRaises(ValidationError):
            self.return_service.create_return(so, data)

    def test_receive_return_restores_stock(self):
        so = self._create_confirmed_so(10)
        item = so.items.first()

        self.pvw.refresh_from_db()
        stock_after_confirm = self.pvw.physical_qty

        data = {
            "reason": "Defective",
            "items": [{"sales_order_item_id": str(item.id), "quantity": 3}],
        }
        ret = self.return_service.create_return(so, data)
        ret.status = SalesReturn.ReturnStatus.APPROVED
        ret.save()
        self.return_service.receive_return(ret)

        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, stock_after_confirm + 3)

    def test_receive_return_reverses_cogs(self):
        so = self._create_confirmed_so(10)
        item = so.items.first()

        self.cogs_layer.refresh_from_db()
        cogs_remaining_after_confirm = self.cogs_layer.remaining_qty

        data = {
            "reason": "Defective",
            "items": [{"sales_order_item_id": str(item.id), "quantity": 3}],
        }
        ret = self.return_service.create_return(so, data)
        ret.status = SalesReturn.ReturnStatus.APPROVED
        ret.save()
        self.return_service.receive_return(ret)

        self.cogs_layer.refresh_from_db()
        self.assertEqual(self.cogs_layer.remaining_qty, cogs_remaining_after_confirm + 3)


class SalesOrderFullLifecycleTest(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.product = ProductFactory(company=self.company)
        self.variant = ProductVariantFactory(
            product=self.product, company=self.company, total_available_qty=100
        )
        self.pvw = ProductVariantWarehouseFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=100,
        )
        self.cogs_layer = ProductCogsFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            original_qty=100,
            remaining_qty=100,
            cogs_amount=50000,
        )
        self.service = SalesOrderService()
        self.return_service = SalesReturnService()

    def test_full_lifecycle_pending_to_completed(self):
        so = SalesOrderFactory(warehouse=self.warehouse, company=self.company)
        SalesOrderItemFactory(
            sales_order=so,
            product_variant=self.variant,
            company=self.company,
            quantity=5,
            selling_price=100000,
        )

        # Confirm
        so = self.service.confirm_order(so)
        self.assertEqual(so.status, SalesOrder.OrderStatus.CONFIRMED)

        # Ship
        so = self.service.update_sales_order(so, {"status": SalesOrder.OrderStatus.SHIPPING})
        self.assertEqual(so.status, SalesOrder.OrderStatus.SHIPPING)

        # Deliver
        so = self.service.update_sales_order(so, {"status": SalesOrder.OrderStatus.DELIVERED})
        self.assertEqual(so.status, SalesOrder.OrderStatus.DELIVERED)

        # Complete
        so = self.service.update_sales_order(so, {"status": SalesOrder.OrderStatus.COMPLETED})
        self.assertEqual(so.status, SalesOrder.OrderStatus.COMPLETED)

        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 95)

    def test_full_lifecycle_with_return(self):
        so = SalesOrderFactory(warehouse=self.warehouse, company=self.company)
        SalesOrderItemFactory(
            sales_order=so,
            product_variant=self.variant,
            company=self.company,
            quantity=10,
            selling_price=100000,
        )

        # Confirm → Ship → Deliver
        so = self.service.confirm_order(so)
        so = self.service.update_sales_order(so, {"status": SalesOrder.OrderStatus.SHIPPING})
        so = self.service.update_sales_order(so, {"status": SalesOrder.OrderStatus.DELIVERED})

        # Create and receive return
        item = so.items.first()
        ret = self.return_service.create_return(
            so,
            {
                "reason": "Customer changed mind",
                "items": [{"sales_order_item_id": str(item.id), "quantity": 3}],
            },
        )
        ret.status = SalesReturn.ReturnStatus.APPROVED
        ret.save()
        self.return_service.receive_return(ret)

        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 93)  # 100 - 10 + 3

        self.cogs_layer.refresh_from_db()
        self.assertEqual(self.cogs_layer.remaining_qty, 93)  # 100 - 10 + 3


class EdgeCaseSalesTests(TestCase):
    """Tests for edge case fixes in sales."""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.product = ProductFactory(company=self.company)
        self.variant = ProductVariantFactory(
            product=self.product, company=self.company, total_available_qty=100
        )
        self.pvw = ProductVariantWarehouseFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=100,
        )
        self.cogs_layer = ProductCogsFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            original_qty=100,
            remaining_qty=100,
            cogs_amount=50000,
        )
        self.so_service = SalesOrderService()
        self.return_service = SalesReturnService()

    def _create_so_with_items(self, qty=5):
        so = SalesOrderFactory(warehouse=self.warehouse, company=self.company)
        SalesOrderItemFactory(
            sales_order=so,
            product_variant=self.variant,
            company=self.company,
            quantity=qty,
            selling_price=100000,
        )
        return so

    # Fix 1: select_for_update in confirm/cancel prevents double confirm
    def test_double_confirm_raises_error(self):
        so = self._create_so_with_items(5)
        self.so_service.confirm_order(so)
        # so still has old status in memory, but DB is CONFIRMED
        with self.assertRaises(ValidationError):
            self.so_service.confirm_order(so)

    def test_double_cancel_raises_error(self):
        so = self._create_so_with_items(5)
        self.so_service.cancel_order(so)
        with self.assertRaises(ValidationError):
            self.so_service.cancel_order(so)

    # Fix 6: FIFO idempotency
    def test_fifo_consumption_idempotent(self):
        so = self._create_so_with_items(5)
        item = so.items.first()
        cogs_service = CogsConsumptionService()
        details1 = cogs_service.consume_fifo(item, self.warehouse.id)
        details2 = cogs_service.consume_fifo(item, self.warehouse.id)
        self.assertEqual(len(details1), len(details2))
        # COGS layer should only be consumed once
        self.cogs_layer.refresh_from_db()
        self.assertEqual(self.cogs_layer.remaining_qty, 95)

    # Fix 10: Require APPROVED before receiving return
    def test_receive_return_requires_approved_status(self):
        so = self._create_so_with_items(10)
        self.so_service.confirm_order(so)
        item = so.items.first()
        ret = self.return_service.create_return(
            so,
            {
                "reason": "Defective",
                "items": [{"sales_order_item_id": str(item.id), "quantity": 3}],
            },
        )
        # ret is REQUESTED by default
        with self.assertRaises(ValidationError) as ctx:
            self.return_service.receive_return(ret)
        self.assertIn("APPROVED", str(ctx.exception))

    def test_receive_return_works_when_approved(self):
        so = self._create_so_with_items(10)
        self.so_service.confirm_order(so)
        item = so.items.first()
        ret = self.return_service.create_return(
            so,
            {
                "reason": "Defective",
                "items": [{"sales_order_item_id": str(item.id), "quantity": 3}],
            },
        )
        ret.status = SalesReturn.ReturnStatus.APPROVED
        ret.save()
        self.return_service.receive_return(ret)
        ret.refresh_from_db()
        self.assertEqual(ret.status, SalesReturn.ReturnStatus.RECEIVED)

    # Fix 11: Validate SO item quantity and price
    def test_create_so_zero_quantity_raises_error(self):
        data = {
            "warehouse_id": str(self.warehouse.id),
            "company_id": str(self.company.id),
            "order_date": timezone.now(),
            "items": [
                {
                    "product_variant_id": str(self.variant.id),
                    "quantity": 0,
                    "selling_price": 100000,
                    "discount_amount": 0,
                    "commission_fee": 0,
                    "service_fee": 0,
                }
            ],
        }
        with self.assertRaises(ValidationError):
            self.so_service.create_sales_order(data)

    def test_create_so_negative_quantity_raises_error(self):
        data = {
            "warehouse_id": str(self.warehouse.id),
            "company_id": str(self.company.id),
            "order_date": timezone.now(),
            "items": [
                {
                    "product_variant_id": str(self.variant.id),
                    "quantity": -5,
                    "selling_price": 100000,
                    "discount_amount": 0,
                    "commission_fee": 0,
                    "service_fee": 0,
                }
            ],
        }
        with self.assertRaises(ValidationError):
            self.so_service.create_sales_order(data)

    def test_create_so_negative_price_raises_error(self):
        data = {
            "warehouse_id": str(self.warehouse.id),
            "company_id": str(self.company.id),
            "order_date": timezone.now(),
            "items": [
                {
                    "product_variant_id": str(self.variant.id),
                    "quantity": 5,
                    "selling_price": -100,
                    "discount_amount": 0,
                    "commission_fee": 0,
                    "service_fee": 0,
                }
            ],
        }
        with self.assertRaises(ValidationError):
            self.so_service.create_sales_order(data)

    # Fix 8: AR update after return (tested here since it involves sales return)
    def test_ar_updated_after_return_received(self):
        so = self._create_so_with_items(10)
        self.so_service.confirm_order(so)
        so.refresh_from_db()
        # Move to COMPLETED to create AR
        so.status = SalesOrder.OrderStatus.SHIPPING
        so.save()
        so.status = SalesOrder.OrderStatus.DELIVERED
        so.save()
        so.status = SalesOrder.OrderStatus.COMPLETED
        so.save()
        from apps.finance.services.accounts_payable_service import AccountsPayableService

        AccountsPayableService().create_receivable_from_so(so)
        ar = so.receivable
        original_amount = ar.expected_amount

        item = so.items.first()
        ret = self.return_service.create_return(
            so,
            {
                "reason": "Defective",
                "items": [{"sales_order_item_id": str(item.id), "quantity": 3}],
            },
        )
        ret.status = SalesReturn.ReturnStatus.APPROVED
        ret.save()
        self.return_service.receive_return(ret)
        ar.refresh_from_db()
        # AR expected_amount should have been updated (decreased)
        self.assertNotEqual(ar.expected_amount, original_amount)


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


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class SalesParserTest(TestCase):
    """Tests for pure parsing functions in import_sales_parser."""

    def _make_headers(
        self,
        extra: dict[str, str] | None = None,
        omit: list[str] | None = None,
    ) -> tuple:
        """Build a minimal headers tuple covering all required columns."""
        base: dict[str, str] = {
            "nomor paket": "nomor paket",
            "status pesanan": "status pesanan",
            "channel - nama toko": "channel - nama toko",
            "tanggal pesanan dibuat": "tanggal pesanan dibuat",
            "sku master": "sku master",
            "sku marketplace": "sku marketplace",
            "jumlah": "jumlah",
            "harga dibayar": "harga dibayar",
            "subtotal pesanan": "subtotal pesanan",
            "nama pembeli": "nama pembeli",
        }
        if extra:
            base.update(extra)
        if omit:
            for key in omit:
                base.pop(key, None)
        return tuple(base.values())

    def _col_index(self, headers: tuple, col_name: str) -> int:
        return list(headers).index(col_name)

    def test_detect_columns_returns_all_required(self):
        from core.parsers.import_sales_parser import detect_columns

        headers = self._make_headers()
        result = detect_columns(headers)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("nomor_paket_col", result)
        self.assertIn("status_col", result)
        self.assertIn("order_date_col", result)
        self.assertIn("qty_col", result)
        self.assertIn("selling_price_col", result)
        self.assertIn("subtotal_col", result)
        self.assertIn("customer_col", result)

    def test_detect_columns_returns_none_on_missing_required(self):
        from core.parsers.import_sales_parser import detect_columns

        # Remove both SKU columns — should return None
        headers = self._make_headers(omit=["sku master", "sku marketplace"])
        result = detect_columns(headers)
        self.assertIsNone(result)

    def test_detect_columns_returns_none_on_missing_mandatory_col(self):
        from core.parsers.import_sales_parser import detect_columns

        # Remove "jumlah" (qty) — should return None
        headers = self._make_headers(omit=["jumlah"])
        result = detect_columns(headers)
        self.assertIsNone(result)

    def test_parse_order_date_handles_datetime_object(self):
        from datetime import datetime, timezone

        from core.parsers.import_sales_parser import parse_order_date

        naive_dt = datetime(2026, 1, 15, 10, 30, 0)
        result = parse_order_date(naive_dt)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.year, 2026)
        self.assertEqual(result.month, 1)
        self.assertEqual(result.day, 15)
        self.assertEqual(result.tzinfo, timezone.utc)

    def test_parse_order_date_handles_aware_datetime_unchanged(self):
        from datetime import datetime, timezone

        from core.parsers.import_sales_parser import parse_order_date

        aware_dt = datetime(2026, 3, 10, tzinfo=timezone.utc)
        result = parse_order_date(aware_dt)
        self.assertEqual(result, aware_dt)

    def test_parse_order_date_handles_string_formats(self):
        from core.parsers.import_sales_parser import parse_order_date

        result1 = parse_order_date("2026-05-20 14:30:00")
        self.assertIsNotNone(result1)
        assert result1 is not None
        self.assertEqual(result1.year, 2026)
        self.assertEqual(result1.month, 5)
        self.assertEqual(result1.day, 20)

        result2 = parse_order_date("2026-07-01")
        self.assertIsNotNone(result2)
        assert result2 is not None
        self.assertEqual(result2.year, 2026)
        self.assertEqual(result2.month, 7)
        self.assertEqual(result2.day, 1)

    def test_parse_order_date_returns_none_for_none(self):
        from core.parsers.import_sales_parser import parse_order_date

        self.assertIsNone(parse_order_date(None))

    def test_parse_order_date_returns_none_for_invalid_string(self):
        from core.parsers.import_sales_parser import parse_order_date

        self.assertIsNone(parse_order_date("not-a-date"))

    def test_parse_decimal_returns_none_for_blank(self):
        from core.parsers.import_sales_parser import parse_decimal

        self.assertIsNone(parse_decimal(None))
        self.assertIsNone(parse_decimal(""))
        self.assertIsNone(parse_decimal("   "))

    def test_parse_decimal_returns_correct_decimal(self):
        from decimal import Decimal

        from core.parsers.import_sales_parser import parse_decimal

        result = parse_decimal("123456.78")
        self.assertIsNotNone(result)
        self.assertEqual(result, Decimal("123456.78"))

    def test_parse_decimal_handles_integer_values(self):
        from decimal import Decimal

        from core.parsers.import_sales_parser import parse_decimal

        result = parse_decimal(100000)
        self.assertEqual(result, Decimal("100000"))

    def _make_data_row(
        self,
        headers: tuple,
        overrides: dict[str, object],
    ) -> tuple:
        """Build a data row aligned to headers, applying overrides by header name."""
        row = [None] * len(headers)
        for i, h in enumerate(headers):
            if h in overrides:
                row[i] = overrides[h]
        return tuple(row)

    def test_parse_sales_sheet_happy_path_groups_by_order_number(self):
        from core.parsers.import_sales_parser import parse_sales_sheet

        headers = self._make_headers()
        row1 = self._make_data_row(
            headers,
            {
                "nomor paket": "PKG-001",
                "status pesanan": "Completed",
                "channel - nama toko": "Shopee",
                "tanggal pesanan dibuat": "2026-01-15",
                "sku master": "SKU-A",
                "sku marketplace": "MKT-A",
                "jumlah": 2,
                "harga dibayar": 50000,
                "subtotal pesanan": 100000,
                "nama pembeli": "Alice",
            },
        )
        row2 = self._make_data_row(
            headers,
            {
                "nomor paket": "PKG-001",
                "status pesanan": "Completed",
                "channel - nama toko": "Shopee",
                "tanggal pesanan dibuat": "2026-01-15",
                "sku master": "SKU-B",
                "sku marketplace": "MKT-B",
                "jumlah": 1,
                "harga dibayar": 75000,
                "subtotal pesanan": 100000,
                "nama pembeli": "Alice",
            },
        )
        row3 = self._make_data_row(
            headers,
            {
                "nomor paket": "PKG-002",
                "status pesanan": "Delivered",
                "channel - nama toko": "Shopee",
                "tanggal pesanan dibuat": "2026-01-16",
                "sku master": "SKU-C",
                "sku marketplace": "MKT-C",
                "jumlah": 3,
                "harga dibayar": 30000,
                "subtotal pesanan": 90000,
                "nama pembeli": "Bob",
            },
        )
        rows = [headers, row1, row2, row3]
        result = parse_sales_sheet("Sales Test", rows)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].order_number, "PKG-001")
        self.assertEqual(len(result[0].items), 2)
        self.assertEqual(result[1].order_number, "PKG-002")
        self.assertEqual(len(result[1].items), 1)

    def test_parse_sales_sheet_skips_rows_with_no_sku(self):
        from core.parsers.import_sales_parser import parse_sales_sheet

        headers = self._make_headers()
        row1 = self._make_data_row(
            headers,
            {
                "nomor paket": "PKG-001",
                "status pesanan": "Completed",
                "channel - nama toko": "Shopee",
                "tanggal pesanan dibuat": "2026-01-15",
                "sku master": None,
                "sku marketplace": None,
                "jumlah": 2,
                "harga dibayar": 50000,
                "subtotal pesanan": 100000,
                "nama pembeli": "Alice",
            },
        )
        rows = [headers, row1]
        result = parse_sales_sheet("Sales Test", rows)

        # Order is still created but has no items
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0].items), 0)

    def test_parse_sales_sheet_skips_zero_qty_rows(self):
        from core.parsers.import_sales_parser import parse_sales_sheet

        headers = self._make_headers()
        row1 = self._make_data_row(
            headers,
            {
                "nomor paket": "PKG-001",
                "status pesanan": "Completed",
                "channel - nama toko": "Shopee",
                "tanggal pesanan dibuat": "2026-01-15",
                "sku master": "SKU-A",
                "sku marketplace": "MKT-A",
                "jumlah": 0,
                "harga dibayar": 50000,
                "subtotal pesanan": 100000,
                "nama pembeli": "Alice",
            },
        )
        rows = [headers, row1]
        result = parse_sales_sheet("Sales Test", rows)

        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0].items), 0)

    def test_parse_sales_sheet_maps_status_correctly(self):
        from core.parsers.import_sales_parser import parse_sales_sheet

        headers = self._make_headers()
        status_cases = [
            ("Completed", "COMPLETED", True),
            ("Cancellations", "CANCELLED", False),
            ("To_Process", "PENDING", False),
            ("Returns", "RETURNED", False),
        ]
        for raw_status, expected_status, expected_stock in status_cases:
            row = self._make_data_row(
                headers,
                {
                    "nomor paket": f"PKG-{raw_status}",
                    "status pesanan": raw_status,
                    "channel - nama toko": "Shopee",
                    "tanggal pesanan dibuat": "2026-01-15",
                    "sku master": "SKU-A",
                    "sku marketplace": "MKT-A",
                    "jumlah": 1,
                    "harga dibayar": 50000,
                    "subtotal pesanan": 50000,
                    "nama pembeli": "Test",
                },
            )
            result = parse_sales_sheet("Sales Test", [headers, row])
            self.assertEqual(len(result), 1, f"Expected 1 order for status {raw_status}")
            self.assertEqual(result[0].status, expected_status, f"Status mismatch for {raw_status}")
            self.assertEqual(
                result[0].is_stock_affecting,
                expected_stock,
                f"is_stock_affecting mismatch for {raw_status}",
            )

    def test_parse_sales_sheet_skips_missing_required_columns(self):
        from core.parsers.import_sales_parser import parse_sales_sheet

        # Headers without qty column
        headers = self._make_headers(omit=["jumlah"])
        row = self._make_data_row(
            headers,
            {
                "nomor paket": "PKG-001",
                "status pesanan": "Completed",
                "channel - nama toko": "Shopee",
                "tanggal pesanan dibuat": "2026-01-15",
                "sku master": "SKU-A",
                "sku marketplace": "MKT-A",
                "harga dibayar": 50000,
                "subtotal pesanan": 100000,
                "nama pembeli": "Alice",
            },
        )
        rows = [headers, row]
        result = parse_sales_sheet("Sales Test", rows)
        self.assertEqual(result, [])

    def test_parse_sales_sheet_net_revenue_from_total_penjualan(self):
        from core.parsers.import_sales_parser import parse_sales_sheet

        headers = self._make_headers(extra={"total penjualan bersih": "total penjualan bersih"})
        row = self._make_data_row(
            headers,
            {
                "nomor paket": "PKG-001",
                "status pesanan": "Completed",
                "channel - nama toko": "Shopee",
                "tanggal pesanan dibuat": "2026-01-15",
                "sku master": "SKU-A",
                "sku marketplace": "MKT-A",
                "jumlah": 2,
                "harga dibayar": 50000,
                "subtotal pesanan": 100000,
                "nama pembeli": "Alice",
                "total penjualan bersih": 90000,
            },
        )
        rows = [headers, row]
        result = parse_sales_sheet("Sales Test", rows)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].net_revenue, 90000)

    def test_parse_sales_sheet_too_few_rows(self):
        from core.parsers.import_sales_parser import parse_sales_sheet

        result = parse_sales_sheet("Sales Test", [])
        self.assertEqual(result, [])

        headers = self._make_headers()
        result = parse_sales_sheet("Sales Test", [headers])
        self.assertEqual(result, [])

    def test_returned_status_not_stock_affecting(self):
        """RETURNED orders should not trigger FIFO consumption."""
        from core.parsers.import_sales_parser import STOCK_AFFECTING_STATUSES

        self.assertNotIn("RETURNED", STOCK_AFFECTING_STATUSES)

    def test_parse_sales_sheet_duplicate_sku_within_order_both_items_included(self):
        """Two rows with same SKU in one order group should both appear as separate items."""
        from core.parsers.import_sales_parser import parse_sales_sheet

        headers = self._make_headers()
        sku = "SKU-DUPE"
        row1 = self._make_data_row(
            headers,
            {
                "nomor paket": "PKG-001",
                "status pesanan": "Completed",
                "channel - nama toko": "Shopee",
                "tanggal pesanan dibuat": "2026-01-15",
                "sku master": sku,
                "sku marketplace": sku,
                "jumlah": 3,
                "harga dibayar": 50000,
                "subtotal pesanan": 150000,
                "nama pembeli": "Alice",
            },
        )
        row2 = self._make_data_row(
            headers,
            {
                "nomor paket": "PKG-001",
                "status pesanan": "Completed",
                "channel - nama toko": "Shopee",
                "tanggal pesanan dibuat": "2026-01-15",
                "sku master": sku,
                "sku marketplace": sku,
                "jumlah": 2,
                "harga dibayar": 50000,
                "subtotal pesanan": 150000,
                "nama pembeli": "Alice",
            },
        )
        rows = [headers, row1, row2]
        result = parse_sales_sheet("Sales Test 2026", rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0].items), 2)
        self.assertEqual(result[0].items[0].quantity, 3)
        self.assertEqual(result[0].items[1].quantity, 2)


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------


class SalesImportServiceTest(TestCase):
    """Tests for SalesImportService."""

    def setUp(self):
        from apps.catalog.factories import ProductFactory, ProductVariantFactory
        from apps.inventory.factories import ProductCogsFactory, ProductVariantWarehouseFactory
        from core.factories import CompanyFactory, WarehouseFactory

        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.product = ProductFactory(company=self.company)
        self.variant = ProductVariantFactory(product=self.product, company=self.company)
        self.pvw = ProductVariantWarehouseFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=100,
        )
        self.cogs_layer = ProductCogsFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            original_qty=100,
            remaining_qty=100,
            cogs_amount=50000,
        )
        self.variant_map: dict[str, str] = {self.variant.sku_variant_code: str(self.variant.id)}

    def _make_order(
        self,
        order_number: str = "PKG-001",
        status: str = "COMPLETED",
        qty: int = 10,
        price: int = 100000,
    ):
        from datetime import datetime, timezone

        from core.parsers.import_sales_parser import ParsedSalesItem, ParsedSalesOrder

        item = ParsedSalesItem(
            sku_code=self.variant.sku_variant_code,
            quantity=qty,
            selling_price=price,
            line_total=qty * price,
        )
        return ParsedSalesOrder(
            order_number=order_number,
            status=status,
            is_stock_affecting=status in {"COMPLETED", "DELIVERED", "SHIPPING", "CONFIRMED"},
            order_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            customer_name="Test Customer",
            courier_name="JNE",
            tracking_number="TRK001",
            subtotal=qty * price,
            net_revenue=qty * price,
            sheet_name="Sales Test 2026",
            items=[item],
        )

    def test_happy_path_creates_sales_order_and_items(self):
        from apps.sales.models import SalesOrder, SalesOrderItem
        from apps.sales.services.sales_import_service import SalesImportService

        order = self._make_order()
        service = SalesImportService()
        result = service.import_sales(
            company=self.company,
            warehouse=self.warehouse,
            variant_map=self.variant_map,
            parsed_orders=[order],
        )

        self.assertEqual(result.orders_created, 1)
        self.assertEqual(result.items_created, 1)
        self.assertEqual(SalesOrder.objects.filter(company=self.company).count(), 1)
        self.assertEqual(SalesOrderItem.objects.filter(company=self.company).count(), 1)

        so = SalesOrder.objects.get(company=self.company)
        self.assertEqual(so.order_number, "PKG-001")
        self.assertEqual(so.status, "COMPLETED")
        self.assertEqual(so.source_platform, "SHOPEE")
        self.assertEqual(so.subtotal, 10 * 100000)

    def test_fifo_consumption_deducts_cogs_layer(self):
        from apps.sales.models import SalesOrderCogsDetail
        from apps.sales.services.sales_import_service import SalesImportService

        order = self._make_order(qty=10)
        service = SalesImportService()
        service.import_sales(
            company=self.company,
            warehouse=self.warehouse,
            variant_map=self.variant_map,
            parsed_orders=[order],
        )

        self.cogs_layer.refresh_from_db()
        self.assertEqual(self.cogs_layer.remaining_qty, 90)  # 100 - 10
        self.assertEqual(SalesOrderCogsDetail.objects.filter(company=self.company).count(), 1)

    def test_fifo_shortage_logs_warning_and_continues(self):
        from apps.sales.models import SalesOrder, SalesOrderItem
        from apps.sales.services.sales_import_service import SalesImportService

        # Request more than available
        order = self._make_order(qty=200)
        service = SalesImportService()
        result = service.import_sales(
            company=self.company,
            warehouse=self.warehouse,
            variant_map=self.variant_map,
            parsed_orders=[order],
        )

        self.assertEqual(result.fifo_shortage_warnings, 1)
        # Order and item are still created
        self.assertEqual(SalesOrder.objects.filter(company=self.company).count(), 1)
        self.assertEqual(SalesOrderItem.objects.filter(company=self.company).count(), 1)
        # Item cogs stay at 0
        item = SalesOrderItem.objects.get(company=self.company)
        self.assertEqual(item.actual_cogs_total, 0)

    def test_stock_deducted_from_pvw(self):
        from apps.sales.services.sales_import_service import SalesImportService

        order = self._make_order(qty=10)
        service = SalesImportService()
        service.import_sales(
            company=self.company,
            warehouse=self.warehouse,
            variant_map=self.variant_map,
            parsed_orders=[order],
        )

        self.pvw.refresh_from_db()
        # After reset, physical_qty is restored to 100, then 10 are deducted
        self.assertEqual(self.pvw.physical_qty, 90)
        self.assertEqual(self.pvw.outgoing_qty, 10)

    def test_stock_movement_created(self):
        from apps.inventory.models import StockMovement
        from apps.sales.services.sales_import_service import SalesImportService

        order = self._make_order(qty=5)
        service = SalesImportService()
        service.import_sales(
            company=self.company,
            warehouse=self.warehouse,
            variant_map=self.variant_map,
            parsed_orders=[order],
        )

        movements = StockMovement.objects.filter(
            product_variant=self.variant,
            movement_type=StockMovement.MovementType.OUTBOUND,
        )
        self.assertEqual(movements.count(), 1)
        mv = movements.first()
        assert mv is not None
        self.assertEqual(mv.quantity, -5)
        self.assertEqual(mv.reference_number, "PKG-001")

    def test_idempotency_reset_on_second_import(self):
        from apps.sales.models import SalesOrder, SalesOrderItem
        from apps.sales.services.sales_import_service import SalesImportService

        order = self._make_order()
        service = SalesImportService()

        # First import
        service.import_sales(
            company=self.company,
            warehouse=self.warehouse,
            variant_map=self.variant_map,
            parsed_orders=[order],
        )
        self.assertEqual(SalesOrder.objects.filter(company=self.company).count(), 1)

        # Second import with same data — should reset and re-create (one SO, not two)
        service.import_sales(
            company=self.company,
            warehouse=self.warehouse,
            variant_map=self.variant_map,
            parsed_orders=[order],
        )
        self.assertEqual(SalesOrder.objects.filter(company=self.company).count(), 1)
        self.assertEqual(SalesOrderItem.objects.filter(company=self.company).count(), 1)

    def test_non_stock_affecting_order_skips_fifo(self):
        from apps.inventory.models import StockMovement
        from apps.sales.services.sales_import_service import SalesImportService

        order = self._make_order(status="CANCELLED", qty=10)
        service = SalesImportService()
        result = service.import_sales(
            company=self.company,
            warehouse=self.warehouse,
            variant_map=self.variant_map,
            parsed_orders=[order],
        )

        # No FIFO consumed for CANCELLED
        self.cogs_layer.refresh_from_db()
        self.assertEqual(self.cogs_layer.remaining_qty, 100)

        # No OUT movements
        self.assertEqual(
            StockMovement.objects.filter(movement_type=StockMovement.MovementType.OUTBOUND).count(),
            0,
        )
        self.assertEqual(result.fifo_shortage_warnings, 0)

    def test_duplicate_order_number_skipped(self):
        from apps.sales.models import SalesOrder
        from apps.sales.services.sales_import_service import SalesImportService

        order1 = self._make_order(order_number="PKG-001", qty=5)
        order2 = self._make_order(order_number="PKG-001", qty=3)  # same order_number
        service = SalesImportService()
        result = service.import_sales(
            company=self.company,
            warehouse=self.warehouse,
            variant_map=self.variant_map,
            parsed_orders=[order1, order2],
        )

        self.assertEqual(result.orders_skipped_duplicate, 1)
        self.assertEqual(SalesOrder.objects.filter(company=self.company).count(), 1)

    def test_missing_sku_in_variant_map_skipped(self):
        from apps.sales.models import SalesOrder, SalesOrderItem
        from apps.sales.services.sales_import_service import SalesImportService

        # Empty variant map — all items should be skipped
        order = self._make_order(qty=5)
        service = SalesImportService()
        result = service.import_sales(
            company=self.company,
            warehouse=self.warehouse,
            variant_map={},  # empty
            parsed_orders=[order],
        )

        self.assertEqual(result.items_skipped_no_sku, 1)
        self.assertEqual(SalesOrder.objects.filter(company=self.company).count(), 1)
        self.assertEqual(SalesOrderItem.objects.filter(company=self.company).count(), 0)

    def test_decimal_precision_on_selling_price(self):
        from datetime import datetime, timezone
        from decimal import Decimal

        from apps.sales.models import SalesOrderItem
        from apps.sales.services.sales_import_service import SalesImportService
        from core.parsers.import_sales_parser import ParsedSalesItem, ParsedSalesOrder

        # Simulate a price that comes from Decimal arithmetic
        price_dec = Decimal("123456.78")
        price_int = int(price_dec)  # 123456
        item = ParsedSalesItem(
            sku_code=self.variant.sku_variant_code,
            quantity=1,
            selling_price=price_int,
            line_total=price_int,
        )
        order = ParsedSalesOrder(
            order_number="PKG-DEC",
            status="CANCELLED",
            is_stock_affecting=False,
            order_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            customer_name="Dec Test",
            courier_name="",
            tracking_number="",
            subtotal=price_int,
            net_revenue=price_int,
            sheet_name="Test",
            items=[item],
        )
        service = SalesImportService()
        service.import_sales(
            company=self.company,
            warehouse=self.warehouse,
            variant_map=self.variant_map,
            parsed_orders=[order],
        )

        soi = SalesOrderItem.objects.get(company=self.company)
        self.assertEqual(soi.selling_price, 123456)

    def test_so_total_cogs_updated_after_fifo(self):
        from apps.sales.models import SalesOrder
        from apps.sales.services.sales_import_service import SalesImportService

        # cogs_amount=50000, qty=10 → total_cogs=500000
        order = self._make_order(qty=10, price=100000)
        service = SalesImportService()
        service.import_sales(
            company=self.company,
            warehouse=self.warehouse,
            variant_map=self.variant_map,
            parsed_orders=[order],
        )

        so = SalesOrder.objects.get(company=self.company)
        self.assertEqual(so.total_cogs, 500000)
        self.assertEqual(so.gross_profit, so.net_revenue - 500000)

    def test_fifo_consumes_oldest_layer_first(self):
        """Two COGS layers: oldest consumed before newer, costs accumulated correctly."""
        from datetime import datetime, timezone

        from apps.inventory.factories import ProductCogsFactory
        from apps.sales.models import SalesOrder
        from apps.sales.services.sales_import_service import SalesImportService

        # Create two COGS layers: older (cost 40000) and newer (cost 60000)
        layer1 = ProductCogsFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            original_qty=5,
            remaining_qty=5,
            cogs_amount=40000,
            purchase_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        layer2 = ProductCogsFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            original_qty=10,
            remaining_qty=10,
            cogs_amount=60000,
            purchase_date=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )

        parsed = self._make_order(order_number="PKG-FIFO", status="COMPLETED", qty=8, price=100000)
        variant_map = {self.variant.sku_variant_code: str(self.variant.id)}
        service = SalesImportService()
        service.import_sales(
            company=self.company,
            warehouse=self.warehouse,
            variant_map=variant_map,
            parsed_orders=[parsed],
        )

        layer1.refresh_from_db()
        layer2.refresh_from_db()
        # All 5 from layer1 (oldest) consumed, then 3 from layer2
        self.assertEqual(layer1.remaining_qty, 0)
        self.assertEqual(layer2.remaining_qty, 7)

        so = SalesOrder.objects.get(order_number="PKG-FIFO", company=self.company)
        expected_cogs = (5 * 40000) + (3 * 60000)
        self.assertEqual(so.total_cogs, expected_cogs)
