import datetime as dt

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.inventory.factories import (
    ProductCogsFactory,
    ProductFactory,
    ProductVariantFactory,
    ProductVariantWarehouseFactory,
)
from apps.inventory.models import StockMovement
from apps.sales.factories import SalesOrderFactory, SalesOrderItemFactory
from apps.sales.models import SalesOrder, SalesOrderCogsDetail, SalesOrderItem, SalesReturn
from apps.sales.services.cogs_consumption import CogsConsumptionService
from apps.sales.services.sales_service import SalesOrderService, SalesReturnService
from core.factories import CompanyFactory, WarehouseFactory


class SalesOrderAPITest(APITestCase):
    def setUp(self):
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


class SalesOrderDateFilterTest(APITestCase):
    def setUp(self):
        self.client = APIClient()
        user = User.objects.create_user(username="staff", password="password", is_staff=True)
        self.client.force_authenticate(user=user)
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
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
