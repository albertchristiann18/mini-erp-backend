from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.inventory.factories import (
    CategoryFactory,
    CompanyFactory,
    ProductFactory,
    ProductVariantFactory,
    ProductVariantWarehouseFactory,
)
from apps.inventory.models import ProductCogs, ProductVariantWarehouse
from apps.inventory.services.inventory_service import InventoryService
from apps.purchasing.factories import (
    PurchaseOrderDetailFactory,
    PurchaseOrderFactory,
)
from apps.purchasing.models import PurchaseOrder, PurchaseOrderStatusHistory
from apps.purchasing.serializers import PurchaseOrderReadSerializer, PurchaseOrderUpdateSerializer
from apps.purchasing.services.purchasing_service import PurchaseOrderService
from core.factories import WarehouseFactory
from core.models import UserProfile


class PurchaseOrderAPITest(TestCase):
    """API test cases for Purchase Orders"""

    def setUp(self):
        from core.models import UserProfile

        self.client = APIClient()
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.user = User.objects.create_user(
            username="po_test_user", password="password", is_staff=True
        )
        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_get_single_po_with_details(self):
        """Get 1 PO and the details"""
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        PurchaseOrderDetailFactory(purchase_order=po, product_variant=self.product_variant)

        response = self.client.get(f"/purchase-order/{po.id}/", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], str(po.id))
        self.assertEqual(len(response.data["order_details"]), 1)
        detail = response.data["order_details"][0]
        self.assertIn("variant_id", detail)
        self.assertEqual(str(po.order_details.first().product_variant.id), detail["variant_id"])

    def test_get_list_of_two_pos(self):
        """Get list of 2 POs"""
        PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        po2 = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)

        response = self.client.get("/purchase-order/", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data["results"]), 2)
        # Default ordering is -cdate (newest first), so po2 comes first
        self.assertEqual(response.data["results"][0]["id"], str(po2.id))

    def test_delivery_fee_idr_in_list_response(self):
        """delivery_fee_idr should be delivery_fee * exchange_rate in list response"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            delivery_fee=300,
            exchange_rate=2250,
        )

        response = self.client.get("/purchase-order/", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        po_data = next(r for r in response.data["results"] if r["id"] == str(po.id))
        self.assertEqual(po_data["delivery_fee_idr"], 675000)

    def test_create_po(self):
        """Create a PO"""
        payload = {
            "warehouse_id": str(self.warehouse.id),
            "company_id": str(self.company.id),
            "supplier_name": "Test Supplier",
            "forwarder_name": "Test Forwarder",
            "shop_services": "Test Shop",
            "commission_fee_pct": 10,
            "delivery_fee": 100,
            "currency": "RMB",
            "exchange_rate": 2200,
            "total_ordered_qty": 100,
            "total_amount": 1000000,
            "order_details": [
                {
                    "product_variant_id": str(self.product_variant.id),
                    "ordered_qty": 100,
                    "unit_price_foreign": 100,
                }
            ],
        }

        response = self.client.post("/purchase-order/", payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        purchase_order = PurchaseOrder.objects.last()
        self.assertTrue(purchase_order.purchase_order_number.startswith("PO-2026-"))

    def test_po_full_lifecycle_to_completed(self):
        """End-to-end test: Create PO and transition through all statuses to COMPLETED.

        Flow: DRAFT -> ORDERED -> SHIPPED -> DELIVERED -> COMPLETED
        Tests API for creation/retrieval and service for status transitions.
        """
        payload = {
            "warehouse_id": str(self.warehouse.id),
            "company_id": str(self.company.id),
            "supplier_name": "Test Supplier",
            "forwarder_name": "Test Forwarder",
            "shop_services": "Test Service",
            "commission_fee_pct": 10,
            "delivery_fee": 100,
            "currency": "RMB",
            "exchange_rate": 2200,
            "cbm": 1,
            "weight": 10,
            "shipping_fee": 1000,
            "order_details": [
                {
                    "product_variant_id": str(self.product_variant.id),
                    "ordered_qty": 50,
                    "unit_price_foreign": 15,
                }
            ],
        }

        response = self.client.post("/purchase-order/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        po = PurchaseOrder.objects.last()
        self.assertEqual(po.status, PurchaseOrder.POStatus.DRAFT)

        response = self.client.get(f"/purchase-order/{po.id}/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["order_details"]), 1)
        self.assertEqual(response.data["order_details"][0]["ordered_qty"], 50)

        service = PurchaseOrderService()
        with patch("apps.purchasing.serializers.compress_pdf_file"):
            service.update_purchase_order(
                po,
                {
                    "status": PurchaseOrder.POStatus.ORDERED,
                    "purchase_order_invoice_file": "invoice.pdf",
                    "invoice_number": "INV-001",
                    "invoice_date": date(2026, 1, 15),
                },
            )
        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.POStatus.ORDERED)

        pvw = ProductVariantWarehouse.objects.get(
            product_variant=self.product_variant, warehouse=self.warehouse
        )
        self.assertEqual(pvw.incoming_qty, 50)

        with patch("apps.purchasing.serializers.compress_pdf_file"):
            service.update_purchase_order(po, {"status": PurchaseOrder.POStatus.SHIPPED})
        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.POStatus.SHIPPED)

        detail = po.order_details.first()
        with patch("apps.purchasing.serializers.compress_pdf_file"):
            service.update_purchase_order(
                po,
                {
                    "status": PurchaseOrder.POStatus.DELIVERED,
                    "delivery_date": date(2026, 1, 20),
                    "delivery_order_number": "DO-001",
                    "order_details": [
                        {
                            "id": str(detail.id),
                            "product_variant_id": str(self.product_variant.id),
                            "ordered_qty": 50,
                            "received_qty": 50,
                            "received_date": "2026-01-20",
                            "unit_price_foreign": 15,
                            "discounted_unit_price_foreign": 15,
                        }
                    ],
                },
            )
        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.POStatus.DELIVERED)

        pvw.refresh_from_db()
        self.assertEqual(pvw.physical_qty, 50)
        self.assertEqual(pvw.incoming_qty, 0)

        cogs_count = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).count()
        self.assertEqual(cogs_count, 1)
        cogs = ProductCogs.objects.get(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        )
        self.assertEqual(cogs.cogs_amount, 33000)
        self.assertEqual(cogs.original_qty, 50)
        self.assertEqual(cogs.remaining_qty, 50)

        with patch("apps.purchasing.serializers.compress_pdf_file"):
            service.update_purchase_order(po, {"status": PurchaseOrder.POStatus.COMPLETED})
        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.POStatus.COMPLETED)

    def test_cogs_created_for_each_po_even_same_price(self):
        """Test that COGS is created for each PO, even with same cogs_amount.

        Scenario:
        1. PO1: Create with product, move to DELIVERED -> COGS created (cogs_amount = 33000)
        2. PO2: Create with same product, same price, move to DELIVERED -> Another COGS created
        3. Verify both COGS records exist with same cogs_amount but         different purchase dates and reference numbers
        """
        service = PurchaseOrderService()

        po1_payload = {
            "warehouse_id": str(self.warehouse.id),
            "company_id": str(self.company.id),
            "supplier_name": "Supplier A",
            "forwarder_name": "Forwarder A",
            "shop_services": "Service A",
            "commission_fee_pct": 10,
            "delivery_fee": 100,
            "currency": "RMB",
            "exchange_rate": 2200,
            "cbm": 1,
            "weight": 10,
            "shipping_fee": 1000,
            "order_details": [
                {
                    "product_variant_id": str(self.product_variant.id),
                    "ordered_qty": 50,
                    "unit_price_foreign": 15,
                }
            ],
        }

        response = self.client.post("/purchase-order/", po1_payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        po1 = PurchaseOrder.objects.last()
        self.assertEqual(po1.status, PurchaseOrder.POStatus.DRAFT)

        with patch("apps.purchasing.serializers.compress_pdf_file"):
            service.update_purchase_order(
                po1,
                {
                    "status": PurchaseOrder.POStatus.ORDERED,
                    "purchase_order_invoice_file": "invoice.pdf",
                    "invoice_number": "INV-001",
                    "invoice_date": date(2026, 1, 15),
                },
            )

        with patch("apps.purchasing.serializers.compress_pdf_file"):
            service.update_purchase_order(po1, {"status": PurchaseOrder.POStatus.SHIPPED})

        detail1 = po1.order_details.first()
        with patch("apps.purchasing.serializers.compress_pdf_file"):
            service.update_purchase_order(
                po1,
                {
                    "status": PurchaseOrder.POStatus.DELIVERED,
                    "delivery_date": date(2026, 1, 20),
                    "delivery_order_number": "DO-001",
                    "order_details": [
                        {
                            "id": str(detail1.id),
                            "product_variant_id": str(self.product_variant.id),
                            "ordered_qty": 50,
                            "received_qty": 50,
                            "received_date": "2026-01-20",
                            "unit_price_foreign": 15,
                            "discounted_unit_price_foreign": 15,
                        }
                    ],
                },
            )

        po1.refresh_from_db()

        cogs1 = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
        )
        self.assertEqual(cogs1.count(), 1)
        self.assertEqual(cogs1.first().cogs_amount, 33000)

    def test_can_edit_ordered_qty_on_ordered_po(self):
        """PATCH ordered_qty on ORDERED PO should return 200 — qty is now editable in ORDERED status."""
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        detail = PurchaseOrderDetailFactory(
            purchase_order=po, product_variant=self.product_variant, ordered_qty=100
        )
        service = PurchaseOrderService()
        with patch("apps.purchasing.serializers.compress_pdf_file"):
            service.update_purchase_order(
                po,
                {
                    "status": PurchaseOrder.POStatus.ORDERED,
                    "purchase_order_invoice_file": "invoice.pdf",
                    "invoice_number": "INV-001",
                    "invoice_date": date(2026, 1, 15),
                    "exchange_rate": 2200,
                    "commission_fee_pct": 10,
                    "forwarder_name": "Forwarder",
                    "supplier_name": "Supplier",
                    "shop_services": "Service",
                    "delivery_fee": 100,
                },
            )
        po.refresh_from_db()
        response = self.client.patch(
            f"/purchase-order/{po.id}/",
            {"order_details": [{"id": str(detail.id), "ordered_qty": 999}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        detail.refresh_from_db()
        self.assertEqual(detail.ordered_qty, 999)

    def test_cannot_edit_unit_price_on_ordered_po(self):
        """PATCH unit_price_foreign on ORDERED PO should return 400."""
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        detail = PurchaseOrderDetailFactory(
            purchase_order=po, product_variant=self.product_variant, ordered_qty=100
        )
        service = PurchaseOrderService()
        with patch("apps.purchasing.serializers.compress_pdf_file"):
            service.update_purchase_order(
                po,
                {
                    "status": PurchaseOrder.POStatus.ORDERED,
                    "purchase_order_invoice_file": "invoice.pdf",
                    "invoice_number": "INV-001",
                    "invoice_date": date(2026, 1, 15),
                    "exchange_rate": 2200,
                    "commission_fee_pct": 10,
                    "forwarder_name": "Forwarder",
                    "supplier_name": "Supplier",
                    "shop_services": "Service",
                    "delivery_fee": 100,
                },
            )
        po.refresh_from_db()
        response = self.client.patch(
            f"/purchase-order/{po.id}/",
            {"order_details": [{"id": str(detail.id), "unit_price_foreign": 9999}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_can_add_new_item_to_ordered_po(self):
        """PATCH with new item on ORDERED PO should succeed and create the item."""
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        detail1 = PurchaseOrderDetailFactory(
            purchase_order=po, product_variant=self.product_variant, ordered_qty=100
        )
        service = PurchaseOrderService()
        with patch("apps.purchasing.serializers.compress_pdf_file"):
            service.update_purchase_order(
                po,
                {
                    "status": PurchaseOrder.POStatus.ORDERED,
                    "purchase_order_invoice_file": "invoice.pdf",
                    "invoice_number": "INV-001",
                    "invoice_date": date(2026, 1, 15),
                    "exchange_rate": 2200,
                    "commission_fee_pct": 10,
                    "forwarder_name": "Forwarder",
                    "supplier_name": "Supplier",
                    "shop_services": "Service",
                    "delivery_fee": 100,
                },
            )
        po.refresh_from_db()
        product2 = ProductFactory(category=self.category, company=self.company)
        product_variant2 = ProductVariantFactory(product=product2)
        response = self.client.patch(
            f"/purchase-order/{po.id}/",
            {
                "order_details": [
                    {"id": str(detail1.id)},
                    {
                        "product_variant_id": str(product_variant2.id),
                        "ordered_qty": 50,
                        "unit_price_foreign": 100.0,
                    },
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        po.refresh_from_db()
        self.assertEqual(po.order_details.count(), 2)

    def test_can_remove_item_from_ordered_po(self):
        """PATCH with fewer items on ORDERED PO removes omitted items."""
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        product2 = ProductFactory(category=self.category, company=self.company)
        product_variant2 = ProductVariantFactory(product=product2)
        detail1 = PurchaseOrderDetailFactory(
            purchase_order=po, product_variant=self.product_variant, ordered_qty=50
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=product_variant2,
            ordered_qty=50,
        )
        service = PurchaseOrderService()
        with patch("apps.purchasing.serializers.compress_pdf_file"):
            service.update_purchase_order(
                po,
                {
                    "status": PurchaseOrder.POStatus.ORDERED,
                    "purchase_order_invoice_file": "invoice.pdf",
                    "invoice_number": "INV-001",
                    "invoice_date": date(2026, 1, 15),
                    "exchange_rate": 2200,
                    "commission_fee_pct": 10,
                    "forwarder_name": "Forwarder",
                    "supplier_name": "Supplier",
                    "shop_services": "Service",
                    "delivery_fee": 100,
                },
            )
        po.refresh_from_db()
        response = self.client.patch(
            f"/purchase-order/{po.id}/",
            {"order_details": [{"id": str(detail1.id)}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        po.refresh_from_db()
        self.assertEqual(po.order_details.count(), 1)

    def test_new_item_added_to_ordered_po_has_prices_calculated(self):
        """New item added to ORDERED PO gets prices calculated using exchange_rate."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            exchange_rate=2000,
        )
        detail1 = PurchaseOrderDetailFactory(
            purchase_order=po, product_variant=self.product_variant, ordered_qty=5
        )
        service = PurchaseOrderService()
        with patch("apps.purchasing.serializers.compress_pdf_file"):
            service.update_purchase_order(
                po,
                {
                    "status": PurchaseOrder.POStatus.ORDERED,
                    "purchase_order_invoice_file": "invoice.pdf",
                    "invoice_number": "INV-001",
                    "invoice_date": date(2026, 1, 15),
                    "exchange_rate": 2000,
                    "commission_fee_pct": 10,
                    "forwarder_name": "Forwarder",
                    "supplier_name": "Supplier",
                    "shop_services": "Service",
                    "delivery_fee": 100,
                },
            )
        po.refresh_from_db()
        product2 = ProductFactory(category=self.category, company=self.company)
        product_variant2 = ProductVariantFactory(product=product2)
        response = self.client.patch(
            f"/purchase-order/{po.id}/",
            {
                "order_details": [
                    {"id": str(detail1.id)},
                    {
                        "product_variant_id": str(product_variant2.id),
                        "ordered_qty": 5,
                        "unit_price_foreign": 10.0,
                    },
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        po.refresh_from_db()
        new_detail = po.order_details.exclude(id=detail1.id).first()
        self.assertIsNotNone(new_detail)
        self.assertEqual(new_detail.unit_price_base, 20000)  # 10.0 * 2000
        self.assertEqual(new_detail.discounted_total_price_base, 100000)  # 20000 * 5

    def test_shipped_po_cannot_add_items(self):
        """PATCH with new item on SHIPPED PO should return 400."""
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        PurchaseOrderDetailFactory(
            purchase_order=po, product_variant=self.product_variant, ordered_qty=100
        )
        service = PurchaseOrderService()
        with patch("apps.purchasing.serializers.compress_pdf_file"):
            service.update_purchase_order(
                po,
                {
                    "status": PurchaseOrder.POStatus.ORDERED,
                    "purchase_order_invoice_file": "invoice.pdf",
                    "invoice_number": "INV-001",
                    "invoice_date": date(2026, 1, 15),
                    "exchange_rate": 2200,
                    "commission_fee_pct": 10,
                    "forwarder_name": "Forwarder",
                    "supplier_name": "Supplier",
                    "shop_services": "Service",
                    "delivery_fee": 100,
                },
            )
        po.refresh_from_db()
        with patch("apps.purchasing.serializers.compress_pdf_file"):
            service.update_purchase_order(
                po,
                {
                    "status": PurchaseOrder.POStatus.SHIPPED,
                    "purchase_order_invoice_file": "invoice.pdf",
                    "invoice_number": "INV-001",
                    "invoice_date": date(2026, 1, 15),
                    "exchange_rate": 2200,
                    "commission_fee_pct": 10,
                    "forwarder_name": "Forwarder",
                    "supplier_name": "Supplier",
                    "shop_services": "Service",
                    "delivery_fee": 100,
                },
            )
        po.refresh_from_db()
        product2 = ProductFactory(category=self.category, company=self.company)
        product_variant2 = ProductVariantFactory(product=product2)
        response = self.client.patch(
            f"/purchase-order/{po.id}/",
            {
                "order_details": [
                    {
                        "product_variant_id": str(product_variant2.id),
                        "ordered_qty": 50,
                        "unit_price_foreign": 100,
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class PurchaseOrderServiceTest(TestCase):
    """Unit tests for PurchaseOrderService"""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.service = PurchaseOrderService()

    def test_multiple_po_same_product_incoming_tracking(self):
        """Test that incoming_qty is tracked correctly when multiple POs exist for same product.

        Scenario:
        1. PO1: ordered 20 -> status ORDERED (incoming = 20)
        2. PO2: ordered 15 -> status ORDERED (incoming = 35 total, 20 from PO1 + 15 from PO2)
        3. PO2: status DELIVERED with received=10 (incoming = 25, physical = 10)
        4. PO2: status COMPLETED (incoming = 20 from PO1, physical = 10)
        """
        po1_data = {
            "warehouse_id": str(self.warehouse.id),
            "company_id": str(self.company.id),
            "supplier_name": "Test Supplier",
            "forwarder_name": "Test Forwarder",
            "shop_services": "Test Service",
            "commission_fee_pct": 10,
            "delivery_fee": 100,
            "currency": "RMB",
            "exchange_rate": 2200,
            "cbm": 1,
            "weight": 10,
            "shipping_fee_per_cbm": 100,
            "order_details": [
                {
                    "product_variant_id": str(self.product_variant.id),
                    "ordered_qty": 20,
                    "unit_price_foreign": 10,
                }
            ],
        }

        po1 = self.service.create_purchase_order(po1_data)

        with patch("apps.purchasing.serializers.compress_pdf_file"):
            self.service.update_purchase_order(
                po1,
                {
                    "status": PurchaseOrder.POStatus.ORDERED,
                    "purchase_order_invoice_file": "invoice.pdf",
                    "invoice_number": "INV-001",
                    "invoice_date": date.today(),
                },
            )

        self.assertEqual(po1.status, PurchaseOrder.POStatus.ORDERED)

        pvw = ProductVariantWarehouse.objects.get(
            product_variant=self.product_variant, warehouse=self.warehouse
        )
        self.assertEqual(pvw.incoming_qty, 20)
        self.assertEqual(pvw.physical_qty, 0)

        po2_data = {
            "warehouse_id": str(self.warehouse.id),
            "company_id": str(self.company.id),
            "supplier_name": "Test Supplier 2",
            "forwarder_name": "Test Forwarder",
            "shop_services": "Test Service",
            "commission_fee_pct": 10,
            "delivery_fee": 100,
            "currency": "RMB",
            "exchange_rate": 2200,
            "cbm": 1,
            "weight": 10,
            "shipping_fee_per_cbm": 100,
            "order_details": [
                {
                    "product_variant_id": str(self.product_variant.id),
                    "ordered_qty": 15,
                    "unit_price_foreign": 10,
                }
            ],
        }

        po2 = self.service.create_purchase_order(po2_data)

        with patch("apps.purchasing.serializers.compress_pdf_file"):
            self.service.update_purchase_order(
                po2,
                {
                    "status": PurchaseOrder.POStatus.ORDERED,
                    "purchase_order_invoice_file": "invoice2.pdf",
                    "invoice_number": "INV-002",
                    "invoice_date": date.today(),
                },
            )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 35)

        with patch("apps.purchasing.serializers.compress_pdf_file"):
            self.service.update_purchase_order(
                po2,
                {
                    "status": PurchaseOrder.POStatus.SHIPPED,
                    "delivery_order_number": "DO-002",
                },
            )

        detail = po2.order_details.first()
        with patch("apps.purchasing.serializers.compress_pdf_file"):
            self.service.update_purchase_order(
                po2,
                {
                    "status": PurchaseOrder.POStatus.DELIVERED,
                    "delivery_order_invoice_file": "doi.pdf",
                    "order_details": [
                        {
                            "id": str(detail.id),
                            "product_variant_id": str(self.product_variant.id),
                            "ordered_qty": 15,
                            "received_qty": 10,
                            "received_date": str(date.today() + timedelta(days=1)),
                        }
                    ],
                },
            )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 25)
        self.assertEqual(pvw.physical_qty, 10)

        with patch("apps.purchasing.serializers.compress_pdf_file"):
            self.service.update_purchase_order(
                po2,
                {"status": PurchaseOrder.POStatus.COMPLETED},
            )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 20)
        self.assertEqual(pvw.physical_qty, 10)

    def test_cogs_created_for_each_po_even_same_price(self):
        """Test that COGS is created for each PO, even with same cogs_amount.

        Scenario:
        1. PO1: Create with product, move to DELIVERED -> COGS created (cogs_amount = 33000)
        2. PO2: Create with same product, same price, move to DELIVERED -> Another COGS created
        3. Verify both COGS records exist with same cogs_amount but different purchase dates and reference numbers
        """
        po1_payload = {
            "warehouse_id": str(self.warehouse.id),
            "company_id": str(self.company.id),
            "supplier_name": "Supplier A",
            "forwarder_name": "Forwarder A",
            "shop_services": "Service A",
            "commission_fee_pct": 10,
            "delivery_fee": 100,
            "currency": "RMB",
            "exchange_rate": 2200,
            "cbm": 1,
            "weight": 10,
            "shipping_fee": 1000,
            "order_details": [
                {
                    "product_variant_id": str(self.product_variant.id),
                    "ordered_qty": 50,
                    "unit_price_foreign": 15,
                }
            ],
        }

        po1 = self.service.create_purchase_order(po1_payload)
        po1.refresh_from_db()

        with patch("apps.purchasing.serializers.compress_pdf_file"):
            self.service.update_purchase_order(
                po1,
                {
                    "status": PurchaseOrder.POStatus.ORDERED,
                    "purchase_order_invoice_file": "invoice.pdf",
                    "invoice_number": "INV-001",
                    "invoice_date": date(2026, 1, 15),
                },
            )

        with patch("apps.purchasing.serializers.compress_pdf_file"):
            self.service.update_purchase_order(po1, {"status": PurchaseOrder.POStatus.SHIPPED})

        detail1 = po1.order_details.first()
        with patch("apps.purchasing.serializers.compress_pdf_file"):
            self.service.update_purchase_order(
                po1,
                {
                    "status": PurchaseOrder.POStatus.DELIVERED,
                    "delivery_date": date(2026, 1, 20),
                    "delivery_order_number": "DO-001",
                    "order_details": [
                        {
                            "id": str(detail1.id),
                            "product_variant_id": str(self.product_variant.id),
                            "ordered_qty": 50,
                            "received_qty": 50,
                            "received_date": "2026-01-20",
                            "unit_price_foreign": 15,
                            "discounted_unit_price_foreign": 15,
                        }
                    ],
                },
            )

        po1.refresh_from_db()

        cogs1 = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
        )
        self.assertEqual(cogs1.count(), 1)
        self.assertEqual(cogs1.first().cogs_amount, 33000)

        po2_payload = {
            "warehouse_id": str(self.warehouse.id),
            "company_id": str(self.company.id),
            "supplier_name": "Supplier B",
            "forwarder_name": "Forwarder B",
            "shop_services": "Service B",
            "commission_fee_pct": 10,
            "delivery_fee": 100,
            "currency": "RMB",
            "exchange_rate": 2200,
            "cbm": 1,
            "weight": 10,
            "shipping_fee": 1000,
            "order_details": [
                {
                    "product_variant_id": str(self.product_variant.id),
                    "ordered_qty": 50,
                    "unit_price_foreign": 15,
                }
            ],
        }

        po2 = self.service.create_purchase_order(po2_payload)
        po2.refresh_from_db()

        with patch("apps.purchasing.serializers.compress_pdf_file"):
            self.service.update_purchase_order(
                po2,
                {
                    "status": PurchaseOrder.POStatus.ORDERED,
                    "purchase_order_invoice_file": "invoice2.pdf",
                    "invoice_number": "INV-002",
                    "invoice_date": date(2026, 2, 15),
                },
            )

        po2.refresh_from_db()

        with patch("apps.purchasing.serializers.compress_pdf_file"):
            self.service.update_purchase_order(po2, {"status": PurchaseOrder.POStatus.SHIPPED})

        detail2 = po2.order_details.first()
        with patch("apps.purchasing.serializers.compress_pdf_file"):
            self.service.update_purchase_order(
                po2,
                {
                    "status": PurchaseOrder.POStatus.DELIVERED,
                    "delivery_date": date(2026, 2, 20),
                    "delivery_order_number": "DO-002",
                    "order_details": [
                        {
                            "id": str(detail2.id),
                            "product_variant_id": str(self.product_variant.id),
                            "ordered_qty": 50,
                            "received_qty": 50,
                            "received_date": "2026-02-20",
                            "unit_price_foreign": 15,
                            "discounted_unit_price_foreign": 15,
                        }
                    ],
                },
            )

        cogs_all = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
        )
        self.assertEqual(cogs_all.count(), 2)

        cogs_for_po1 = cogs_all.filter(reference_number=po1.purchase_order_number).first()
        cogs_for_po2 = cogs_all.filter(reference_number=po2.purchase_order_number).first()
        self.assertIsNotNone(cogs_for_po1)
        self.assertIsNotNone(cogs_for_po2)
        self.assertEqual(cogs_for_po1.cogs_amount, cogs_for_po2.cogs_amount)
        self.assertEqual(cogs_for_po1.cogs_amount, 33000)
        self.assertEqual(cogs_for_po2.cogs_amount, 33000)
        self.assertEqual(cogs_for_po1.purchase_date, date(2026, 1, 15))
        self.assertEqual(cogs_for_po2.purchase_date, date(2026, 2, 15))

    def test_decrease_received_qty_fails_when_physical_qty_insufficient(self):
        """Test that decreasing received_qty fails when physical qty already sold."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
            received_qty=10,
            updated_qty=10,
        )

        ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            incoming_qty=90,
            physical_qty=3,
        )

        po.refresh_from_db()

        with patch("apps.purchasing.serializers.compress_pdf_file"):
            with self.assertRaises(ValidationError) as context:
                self.service.update_purchase_order(
                    po,
                    {
                        "status": PurchaseOrder.POStatus.DELIVERED,
                        "order_details": [
                            {
                                "id": str(detail.id),
                                "product_variant_id": str(self.product_variant.id),
                                "ordered_qty": 100,
                                "received_qty": 5,
                                "updated_qty": 10,
                                "received_date": str(date.today() + timedelta(days=1)),
                            }
                        ],
                    },
                )

        self.assertIn("Cannot decrease received_qty", str(context.exception))

    def test_received_qty_exceeds_ordered_requires_remarks(self):
        """Test that received_qty > ordered_qty requires remarks."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
            received_qty=10,
            updated_qty=10,
        )

        ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            incoming_qty=90,
            physical_qty=10,
        )

        po.refresh_from_db()

        with patch("apps.purchasing.serializers.compress_pdf_file"):
            with self.assertRaises(ValidationError) as context:
                self.service.update_purchase_order(
                    po,
                    {
                        "status": PurchaseOrder.POStatus.DELIVERED,
                        "order_details": [
                            {
                                "id": str(detail.id),
                                "product_variant_id": str(self.product_variant.id),
                                "ordered_qty": 100,
                                "received_qty": 150,
                                "updated_qty": 10,
                                "received_date": str(date.today() + timedelta(days=1)),
                            }
                        ],
                    },
                )

        self.assertIn("Remarks is required", str(context.exception))

    def test_update_po_detail_not_found_in_non_draft_status(self):
        """Test that updating non-existent detail fails in non-DRAFT status."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
        )

        po.refresh_from_db()

        with patch("apps.purchasing.serializers.compress_pdf_file"):
            with self.assertRaises(ValidationError) as context:
                self.service.update_purchase_order(
                    po,
                    {
                        "order_details": [
                            {
                                "id": str(uuid4()),
                                "ordered_qty": 50,
                            }
                        ]
                    },
                )

        self.assertIn("Detail with id", str(context.exception))

    def test_create_purchase_order_success(self):
        """Test successful creation of purchase order"""
        data = {
            "warehouse_id": str(self.warehouse.id),
            "company_id": str(self.company.id),
            "supplier_name": "Test Supplier",
            "total_ordered_qty": 100,
            "total_amount": 1000000,
            "order_details": [
                {
                    "product_variant_id": str(self.product_variant.id),
                    "ordered_qty": 100,
                    "unit_price_foreign": 100,
                }
            ],
        }

        self.service.create_purchase_order(data)

        po = PurchaseOrder.objects.last()
        self.assertIsNotNone(po.purchase_order_number)
        self.assertEqual(po.status, PurchaseOrder.POStatus.DRAFT)
        self.assertEqual(po.order_details.count(), 1)

    def test_update_po_updates_details_when_provided(self):
        """Test that order details are updated when provided"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po, product_variant=self.product_variant, ordered_qty=50
        )

        data = {
            "order_details": [
                {
                    "id": str(detail.id),
                    "ordered_qty": 100,
                }
            ]
        }

        self.service.update_purchase_order(po, data)

        detail.refresh_from_db()
        self.assertEqual(detail.ordered_qty, 100)

    def test_update_po_nonexistent_detail_allowed_in_draft(self):
        """Test that non-existent detail is ignored in DRAFT status (no error raised)"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )

        data = {
            "order_details": [
                {
                    "id": str(uuid4()),
                    "ordered_qty": 100,
                }
            ]
        }

        self.service.update_purchase_order(po, data)

        po.refresh_from_db()
        self.assertEqual(po.order_details.count(), 0)

    def test_update_po_draft_add_new_detail(self):
        """Test adding a new detail to PO in DRAFT status"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )
        detail1 = PurchaseOrderDetailFactory(
            purchase_order=po, product_variant=self.product_variant, ordered_qty=50
        )
        product2 = ProductFactory(category=self.category, company=self.company)
        product_variant2 = ProductVariantFactory(product=product2)

        data = {
            "order_details": [
                {
                    "id": str(detail1.id),
                    "ordered_qty": 50,
                },
                {
                    "product_variant_id": str(product_variant2.id),
                    "ordered_qty": 100,
                    "unit_price_foreign": 100,
                },
            ]
        }

        self.service.update_purchase_order(po, data)

        po.refresh_from_db()
        self.assertEqual(po.order_details.count(), 2)

    def test_update_po_draft_replace_one_detail_with_new_product(self):
        """Test replacing one existing detail with a new product in DRAFT status.

        Scenario: PO has item A and item B. We want to replace item B with item C.
        Result should be: item A and item C.
        """
        product_b = ProductFactory(category=self.category, company=self.company)
        product_variant_b = ProductVariantFactory(product=product_b)

        product_c = ProductFactory(category=self.category, company=self.company)
        product_variant_c = ProductVariantFactory(product=product_c)

        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )
        detail_a = PurchaseOrderDetailFactory(
            purchase_order=po, product_variant=self.product_variant, ordered_qty=50
        )
        detail_b = PurchaseOrderDetailFactory(
            purchase_order=po, product_variant=product_variant_b, ordered_qty=75
        )

        data = {
            "order_details": [
                {
                    "id": str(detail_a.id),
                    "product_variant_id": str(self.product_variant.id),
                    "ordered_qty": 50,
                },
                {
                    "product_variant_id": str(product_variant_c.id),
                    "ordered_qty": 100,
                },
            ]
        }

        self.service.update_purchase_order(po, data)

        po.refresh_from_db()
        self.assertEqual(po.order_details.count(), 2)
        self.assertTrue(po.order_details.filter(id=detail_a.id).exists())
        self.assertFalse(po.order_details.filter(id=detail_b.id).exists())
        self.assertTrue(
            po.order_details.filter(product_variant=product_variant_c, ordered_qty=100).exists()
        )

    def test_update_po_non_draft_update_existing_detail_succeeds(self):
        """Test that updating existing detail in non-DRAFT status succeeds"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po, product_variant=self.product_variant, ordered_qty=50
        )

        data = {
            "order_details": [
                {
                    "id": str(detail.id),
                    "ordered_qty": 100,
                }
            ]
        }

        self.service.update_purchase_order(po, data)

        detail.refresh_from_db()
        self.assertEqual(detail.ordered_qty, 100)

    def test_update_po_delivered_to_completed_success(self):
        """Test that transitioning DELIVERED to COMPLETED succeeds"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
            delivery_date=date.today(),
        )

        data = {"status": PurchaseOrder.POStatus.COMPLETED}

        updated_po = self.service.update_purchase_order(po, data)

        self.assertEqual(updated_po.status, PurchaseOrder.POStatus.COMPLETED)

    def test_update_po_delivered_to_completed_partial_requires_remarks(self):
        """Test that transitioning to COMPLETED with partial delivery requires remarks."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
            delivery_date=date.today(),
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
            received_qty=80,
            updated_qty=80,
        )

        po.refresh_from_db()

        with self.assertRaises(ValidationError) as context:
            self.service.update_purchase_order(
                po,
                {
                    "status": PurchaseOrder.POStatus.COMPLETED,
                    "order_details": [
                        {
                            "id": str(detail.id),
                            "product_variant_id": str(self.product_variant.id),
                            "ordered_qty": 100,
                            "received_qty": 80,
                            "updated_qty": 80,
                        }
                    ],
                },
            )

        self.assertIn("Remarks is required", str(context.exception))

    def test_order_details_totals_match_purchase_order_totals(self):
        """Test that order_details totals match purchase_order totals."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            commission_fee_pct=0,
            delivery_fee=0,
            cbm=0,
            shipping_fee_per_cbm=0,
            exchange_rate=2200,
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
            unit_price_foreign=Decimal("10"),
            discounted_unit_price_foreign=Decimal("10"),
            unit_price_base=22000,
            discounted_unit_price_base=22000,
            total_price_foreign=Decimal("1000"),
            discounted_total_price_foreign=Decimal("1000"),
            total_price_base=220000,
            discounted_total_price_base=220000,
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=50,
            unit_price_foreign=Decimal("20"),
            discounted_unit_price_foreign=Decimal("20"),
            unit_price_base=44000,
            discounted_unit_price_base=44000,
            total_price_foreign=Decimal("1000"),
            discounted_total_price_foreign=Decimal("1000"),
            total_price_base=220000,
            discounted_total_price_base=220000,
        )

        po = self.service.update_purchase_order(po, {})

        po.refresh_from_db()
        self.assertEqual(po.total_ordered_qty, 150)
        self.assertEqual(po.total_received_qty, 0)
        self.assertEqual(po.total_item_amount, 440000)
        self.assertEqual(po.total_order_amount, 440000)
        self.assertEqual(po.total_amount, 440000)

    def test_commission_fee_uses_total_item_rmb(self):
        """Commission fee = commission_fee_pct / 100 * sum(discounted_total_price_foreign) * exchange_rate."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            commission_fee_pct=5,
            delivery_fee=Decimal("0"),
            cbm=Decimal("0"),
            shipping_fee_per_cbm=0,
            exchange_rate=2200,
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=10,
            unit_price_foreign=Decimal("10"),
            discounted_unit_price_foreign=Decimal("10"),
            discounted_total_price_foreign=Decimal("100"),  # 10 * 10
            discounted_total_price_base=220000,
        )
        product2 = ProductFactory(category=self.category, company=self.company)
        product_variant2 = ProductVariantFactory(product=product2)
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=product_variant2,
            ordered_qty=5,
            unit_price_foreign=Decimal("20"),
            discounted_unit_price_foreign=Decimal("20"),
            discounted_total_price_foreign=Decimal("100"),  # 20 * 5
            discounted_total_price_base=220000,
        )

        po = self.service.update_purchase_order(po, {})

        po.refresh_from_db()
        expected_commission = int(
            round(Decimal("5") / Decimal("100") * Decimal("200") * Decimal("2200"))
        )  # 5/100 * 200 * 2200 = 22000
        self.assertEqual(po.commission_fee, expected_commission)

    def test_cost_ratio_cogs_formula(self):
        """cost_ratio_cogs = (delivery_fee_idr + commission_fee + shipping_fee) / total_item_amount * 100."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            delivery_fee=Decimal("50"),
            commission_fee=50000,
            shipping_fee=25000,
            exchange_rate=2200,
            total_item_amount=1000000,
        )

        ratio = po.cost_ratio_cogs()
        expected = (Decimal("50") * 2200 + 50000 + 25000) / Decimal("1000000") * 100
        self.assertEqual(ratio, round(expected, 2))

    def test_read_serializer_includes_cost_ratio_cogs(self):
        """ReadSerializer should include cost_ratio_cogs and shipping_per_qty fields."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            shipping_fee=1000,
            total_ordered_qty=100,
            delivery_fee=Decimal("50"),
            commission_fee=50000,
            exchange_rate=2200,
            total_item_amount=1000000,
        )

        serializer = PurchaseOrderReadSerializer(po)
        self.assertIn("cost_ratio_cogs", serializer.data)
        self.assertIn("shipping_per_qty", serializer.data)


class PurchaseOrderSerializerValidationTest(TestCase):
    """Test cases for PurchaseOrderUpdateSerializer validation"""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.service = PurchaseOrderService()

    def _create_serializer(self, po, data, partial=False):
        return PurchaseOrderUpdateSerializer(po, data=data, partial=partial)

    def test_draft_to_ordered_requires_exchange_rate(self):
        """Test that transitioning DRAFT to ORDERED requires exchange_rate"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.ORDERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("exchange_rate", serializer.errors)

    def test_draft_to_ordered_requires_invoice_file(self):
        """Test that transitioning DRAFT to ORDERED requires invoice file"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.ORDERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("purchase_order_invoice_file", serializer.errors)

    def test_draft_to_ordered_requires_invoice_number(self):
        """Test that transitioning DRAFT to ORDERED requires invoice_number"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            purchase_order_invoice_file="test.pdf",
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.ORDERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("invoice_number", serializer.errors)

    def test_draft_to_ordered_requires_invoice_date(self):
        """Test that transitioning DRAFT to ORDERED requires invoice_date"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            purchase_order_invoice_file="test.pdf",
            invoice_number="INV-001",
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.ORDERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("invoice_date", serializer.errors)

    def test_draft_to_ordered_requires_order_details(self):
        """Test that transitioning DRAFT to ORDERED requires order_details when none exist"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            purchase_order_invoice_file="test.pdf",
            invoice_number="INV-001",
            invoice_date=date.today(),
            commission_fee_pct=Decimal("10"),
            forwarder_name="Test Forwarder",
            supplier_name="Test Supplier",
            shop_services="Test Shop Service",
            delivery_fee=Decimal("0"),
        )

        serializer = self._create_serializer(
            po,
            {
                "status": PurchaseOrder.POStatus.ORDERED,
            },
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("order_details", serializer.errors)

    def test_draft_to_ordered_requires_commission_fee_pct(self):
        """Test that transitioning DRAFT to ORDERED requires commission_fee_pct"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            purchase_order_invoice_file="test.pdf",
            invoice_number="INV-001",
            invoice_date=date.today(),
            forwarder_name="Test Forwarder",
            supplier_name="Test Supplier",
            shop_services="Test Shop Services",
            commission_fee_pct=None,
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.ORDERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("commission_fee_pct", serializer.errors)

    def test_draft_to_ordered_requires_forwarder_name(self):
        """Test that transitioning DRAFT to ORDERED requires forwarder_name"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            purchase_order_invoice_file="test.pdf",
            invoice_number="INV-001",
            invoice_date=date.today(),
            commission_fee_pct=Decimal("10"),
            forwarder_name="",
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.ORDERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("forwarder_name", serializer.errors)

    def test_draft_to_ordered_requires_supplier_name(self):
        """Test that transitioning DRAFT to ORDERED requires supplier_name"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            purchase_order_invoice_file="test.pdf",
            invoice_number="INV-001",
            invoice_date=date.today(),
            commission_fee_pct=Decimal("10"),
            forwarder_name="Test Forwarder",
            shop_services="Test Shop Services",
            supplier_name="",
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.ORDERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("supplier_name", serializer.errors)

    def test_draft_to_ordered_requires_shop_services(self):
        """Test that transitioning DRAFT to ORDERED requires shop_services"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            purchase_order_invoice_file="test.pdf",
            invoice_number="INV-001",
            invoice_date=date.today(),
            commission_fee_pct=Decimal("10"),
            forwarder_name="Test Forwarder",
            supplier_name="Test Supplier",
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.ORDERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("shop_services", serializer.errors)

    def test_draft_to_ordered_requires_delivery_fee(self):
        """Test that transitioning DRAFT to ORDERED requires delivery_fee (can be 0)"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            purchase_order_invoice_file="test.pdf",
            invoice_number="INV-001",
            invoice_date=date.today(),
            commission_fee_pct=Decimal("10"),
            forwarder_name="Test Forwarder",
            supplier_name="Test Supplier",
            shop_services="Test Shop Service",
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.ORDERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("delivery_fee", serializer.errors)

    def test_draft_to_ordered_allows_zero_delivery_fee(self):
        """Test that transitioning DRAFT to ORDERED allows delivery_fee=0"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            purchase_order_invoice_file="test.pdf",
            invoice_number="INV-001",
            invoice_date=date.today(),
            commission_fee_pct=Decimal("10"),
            forwarder_name="Test Forwarder",
            supplier_name="Test Supplier",
            shop_services="Test Shop Service",
            delivery_fee=Decimal("0"),
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
        )

        serializer = self._create_serializer(
            po,
            {
                "status": PurchaseOrder.POStatus.ORDERED,
                "order_details": [
                    {
                        "product_variant_id": str(self.product_variant.id),
                        "ordered_qty": 100,
                    }
                ],
            },
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_ordered_to_shipped_requires_delivery_order_number(self):
        """Test that transitioning ORDERED to SHIPPED requires delivery order number"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.SHIPPED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("delivery_order_number", serializer.errors)

    def test_ordered_to_shipped_requires_delivery_order_file(self):
        """Test that transitioning ORDERED to SHIPPED requires delivery order file"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
            delivery_order_number="DO-001",
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.SHIPPED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("delivery_order_file", serializer.errors)

    def test_ordered_to_shipped_requires_shipping_fee(self):
        """Test that transitioning ORDERED to SHIPPED requires shipping_fee"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
            delivery_order_number="DO-001",
            delivery_order_file="existing_file.pdf",
            cbm=Decimal("1.5"),
            weight=Decimal("10.0"),
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.SHIPPED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("shipping_fee_per_cbm", serializer.errors)

    def test_ordered_to_shipped_requires_cbm(self):
        """Test that transitioning ORDERED to SHIPPED requires cbm"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
            delivery_order_number="DO-001",
            delivery_order_file="existing_file.pdf",
            shipping_fee_per_cbm=1000,
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.SHIPPED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("cbm", serializer.errors)

    def test_ordered_to_shipped_requires_weight(self):
        """Test that transitioning ORDERED to SHIPPED requires weight"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
            delivery_order_number="DO-001",
            delivery_order_file="existing_file.pdf",
            shipping_fee_per_cbm=1000,
            cbm=Decimal("1.5"),
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.SHIPPED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("weight", serializer.errors)

    def test_shipped_to_delivered_requires_delivery_order_invoice(self):
        """Test that transitioning SHIPPED to DELIVERED requires DO invoice"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            delivery_order_number="DO-001",
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.DELIVERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("delivery_order_invoice_file", serializer.errors)

    def test_invalid_status_transition_raises_error(self):
        """Test that invalid status transition raises error"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.DELIVERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("status", serializer.errors)

    def test_exchange_rate_cannot_change_after_ordered(self):
        """Test that exchange_rate cannot be changed after ORDERED status"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
            exchange_rate=2200,
        )

        serializer = self._create_serializer(po, {"exchange_rate": 2300}, partial=True)

        self.assertFalse(serializer.is_valid())
        self.assertIn("exchange_rate", serializer.errors)

    def test_unit_price_foreign_cannot_change_after_ordered(self):
        """Test that unit_price_foreign cannot be changed after ORDERED status"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
            unit_price_foreign=Decimal("10"),
        )

        po.refresh_from_db()
        with self.assertRaises(Exception) as context:
            self.service.update_purchase_order(
                po,
                {
                    "order_details": [
                        {
                            "id": str(detail.id),
                            "ordered_qty": 100,
                            "unit_price_foreign": Decimal("15"),
                        }
                    ]
                },
            )

        self.assertIn("order_details", str(context.exception))
        self.assertIn("unit_price_foreign", str(context.exception))

    def test_discounted_unit_price_foreign_cannot_change_after_ordered(self):
        """Test that discounted_unit_price_foreign cannot be changed after ORDERED status"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
            exchange_rate=2200,
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
            unit_price_foreign=Decimal("10"),
            discounted_unit_price_foreign=Decimal("8"),
        )

        po.refresh_from_db()
        with self.assertRaises(Exception) as context:
            self.service.update_purchase_order(
                po,
                {
                    "order_details": [
                        {
                            "id": str(detail.id),
                            "ordered_qty": 100,
                            "unit_price_foreign": Decimal("10"),
                            "discounted_unit_price_foreign": Decimal("6"),
                        }
                    ]
                },
            )

        self.assertIn("order_details", str(context.exception))
        self.assertIn("discounted_unit_price_foreign", str(context.exception))

    def test_received_qty_cannot_be_filled_if_status_not_shipped_or_delivered(self):
        """Test that received_qty cannot be filled when status is not SHIPPED or DELIVERED"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            purchase_order_invoice_file="invoice.pdf",
            invoice_number="INV-001",
            invoice_date=date.today(),
            supplier_name="Supplier",
            forwarder_name="Forwarder",
            shop_services="service",
            commission_fee_pct=Decimal("10"),
            delivery_fee=100,
        )

        serializer = self._create_serializer(
            po,
            {
                "status": PurchaseOrder.POStatus.ORDERED,
                "order_details": [
                    {
                        "product_variant_id": str(self.product_variant.id),
                        "ordered_qty": 100,
                        "received_qty": 50,
                        "unit_price_foreign": Decimal("10"),
                    }
                ],
            },
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("order_details", serializer.errors)
        self.assertIn("received_qty", str(serializer.errors["order_details"]))

    def test_received_qty_allowed_when_status_is_shipped(self):
        """Test that received_qty can be filled when status is SHIPPED"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
            exchange_rate=2200,
            delivery_order_number="DO-001",
            delivery_order_file="existing_file.pdf",
            shipping_fee_per_cbm=100,
            cbm=Decimal("1"),
            weight=Decimal("10"),
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
            unit_price_foreign=Decimal("10"),
        )
        po.refresh_from_db()

        serializer = self._create_serializer(
            po,
            {
                "status": PurchaseOrder.POStatus.SHIPPED,
                "order_details": [
                    {
                        "id": str(detail.id),
                        "product_variant_id": str(self.product_variant.id),
                        "received_qty": 50,
                    }
                ],
            },
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_received_qty_allowed_when_status_is_delivered(self):
        """Test that received_qty can be filled when status is DELIVERED"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
            delivery_order_invoice_file="existing_file.pdf",
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
            unit_price_foreign=Decimal("10"),
        )
        po.refresh_from_db()

        serializer = self._create_serializer(
            po,
            {
                "status": PurchaseOrder.POStatus.DELIVERED,
                "order_details": [
                    {
                        "id": str(detail.id),
                        "product_variant_id": str(self.product_variant.id),
                        "received_qty": 50,
                    }
                ],
            },
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_cannot_add_new_details_when_shipped(self):
        """Test that adding new details fails when status is SHIPPED"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
            unit_price_foreign=Decimal("10"),
        )

        product2 = ProductFactory(category=self.category, company=self.company)
        product_variant2 = ProductVariantFactory(product=product2)

        po.refresh_from_db()

        serializer = self._create_serializer(
            po,
            {
                "order_details": [
                    {
                        "id": str(detail.id),
                        "product_variant_id": str(self.product_variant.id),
                    },
                    {
                        "product_variant_id": str(product_variant2.id),
                    },
                ],
            },
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("order_details", serializer.errors)
        self.assertIn("Cannot add new details", str(serializer.errors["order_details"]))

    def test_cannot_change_ordered_qty_from_draft_to_ordered(self):
        """Test that ordered_qty cannot be changed when transitioning from DRAFT to ORDERED"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            forwarder_name="Test Forwarder",
            shop_services="Test Service",
            commission_fee_pct=Decimal("10"),
            delivery_fee=100,
            purchase_order_invoice_file="invoice.pdf",
            invoice_number="INV-001",
            invoice_date=date.today(),
            supplier_name="Supplier",
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
            unit_price_foreign=Decimal("10"),
        )

        po.refresh_from_db()

        serializer = self._create_serializer(
            po,
            {
                "status": PurchaseOrder.POStatus.ORDERED,
                "order_details": [
                    {
                        "id": str(detail.id),
                        "product_variant_id": str(self.product_variant.id),
                        "ordered_qty": 150,
                        "unit_price_foreign": Decimal("10"),
                    },
                ],
            },
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("order_details", serializer.errors)
        self.assertIn("ordered_qty", str(serializer.errors["order_details"]))


class EdgeCasePurchasingTests(TestCase):
    """Tests for edge case fixes in purchasing."""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.service = PurchaseOrderService()

    # Fix 5: Enforce PO status transitions
    def test_invalid_po_status_transition_raises_error(self):
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )
        with self.assertRaises(ValidationError) as ctx:
            self.service.update_purchase_order(po, {"status": PurchaseOrder.POStatus.DELIVERED})
        self.assertIn("Cannot transition", str(ctx.exception))

    def test_skip_status_raises_error(self):
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
        )
        with self.assertRaises(ValidationError) as ctx:
            self.service.update_purchase_order(po, {"status": PurchaseOrder.POStatus.DELIVERED})
        self.assertIn("Cannot transition", str(ctx.exception))

    def test_cancel_from_draft_allowed(self):
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )
        result = self.service.update_purchase_order(
            po, {"status": PurchaseOrder.POStatus.CANCELLED}
        )
        self.assertEqual(result.status, PurchaseOrder.POStatus.CANCELLED)

    # Fix 7: AP total syncs with PO recalculate
    def test_ap_total_syncs_on_po_recalculate(self):
        from apps.finance.models import AccountsPayable

        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
            commission_fee_pct=0,
            delivery_fee=0,
            cbm=0,
            shipping_fee_per_cbm=0,
            exchange_rate=2200,
        )
        # Create AP manually
        ap = AccountsPayable.objects.create(
            company=self.company,
            purchase_order=po,
            total_amount=999999,
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
            discounted_total_price_base=220000,
        )
        # Trigger recalculate
        self.service.update_purchase_order(
            po,
            {
                "order_details": [
                    {
                        "id": str(detail.id),
                        "ordered_qty": 100,
                    }
                ]
            },
        )
        ap.refresh_from_db()
        po.refresh_from_db()
        self.assertEqual(ap.total_amount, po.total_amount)


class ForecastFieldsTest(TestCase):
    """Tests for forecast fields and commission_fee_rmb on PurchaseOrder."""

    def setUp(self):
        from core.models import UserProfile

        self.client = APIClient()
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.user = User.objects.create_user(
            username="forecast_test_user", password="password", is_staff=True
        )
        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_forecast_fields_default_null(self):
        """All 4 new fields should be None when creating a PO via factory."""
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        self.assertIsNone(po.forecast_delivery_date)
        self.assertIsNone(po.forecast_cbm)
        self.assertIsNone(po.forecast_shipping_fee_per_cbm)
        self.assertIsNone(po.commission_fee_rmb)

    def test_patch_forecast_fields(self):
        """PATCH a DRAFT PO with forecast fields should save correctly."""
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        payload = {
            "forecast_delivery_date": "2026-08-01",
            "forecast_cbm": "2.500",
            "forecast_shipping_fee_per_cbm": 2000000,
            "commission_fee_rmb": "150.000",
        }

        response = self.client.patch(f"/purchase-order/{po.id}/", payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        po.refresh_from_db()
        self.assertEqual(str(po.forecast_delivery_date), "2026-08-01")
        self.assertEqual(po.forecast_cbm, 2.500)
        self.assertEqual(po.forecast_shipping_fee_per_cbm, 2000000)
        self.assertEqual(po.forecast_shipping_fee, 5000000)
        self.assertEqual(po.commission_fee_rmb, 150.000)

    def test_list_serializer_includes_forecast_delivery_date(self):
        """GET /purchase-order/ should include forecast_delivery_date in results."""
        PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)

        response = self.client.get("/purchase-order/", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("forecast_delivery_date", response.data["results"][0])


class POListSerializerExpansionTest(TestCase):
    """Tests for expanded PurchaseOrderListSerializer fields and filters"""

    def setUp(self):
        from core.models import UserProfile

        self.client = APIClient()
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)

        user = User.objects.create_user(username="staff", password="password", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)

    def test_list_includes_cost_ratio_cogs(self):
        """GET /purchase-order/ should include cost_ratio_cogs in results"""
        PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            procure_amount=1000,
            shipping_fee=500,
            total_item_amount=10000,
        )

        response = self.client.get("/purchase-order/", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("cost_ratio_cogs", response.data["results"][0])

    def test_list_includes_shipping_per_qty(self):
        """GET /purchase-order/ should include shipping_per_qty in results"""
        PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            shipping_fee=1000,
            total_ordered_qty=100,
        )

        response = self.client.get("/purchase-order/", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("shipping_per_qty", response.data["results"][0])

    def test_list_includes_forwarder_name(self):
        """GET /purchase-order/ should include forwarder_name in results"""
        PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            forwarder_name="Test Forwarder",
        )

        response = self.client.get("/purchase-order/", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("forwarder_name", response.data["results"][0])

    def test_list_filter_by_date_from(self):
        """Filter by date_from should return only POs with invoice_date >= date_from"""
        po1 = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            invoice_date=date(2026, 5, 1),
        )
        po2 = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            invoice_date=date(2026, 5, 15),
        )

        response = self.client.get("/purchase-order/", {"date_from": "2026-05-10"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result_ids = [item["id"] for item in response.data["results"]]
        self.assertNotIn(str(po1.id), result_ids)
        self.assertIn(str(po2.id), result_ids)

    def test_list_filter_by_forwarder(self):
        """Filter by forwarder should return only POs with matching forwarder_name"""
        po1 = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            forwarder_name="Forwarder A",
        )
        po2 = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            forwarder_name="Forwarder B",
        )

        response = self.client.get("/purchase-order/", {"forwarder": "Forwarder A"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result_ids = [item["id"] for item in response.data["results"]]
        self.assertIn(str(po1.id), result_ids)
        self.assertNotIn(str(po2.id), result_ids)


class CompanyScopedViewsTest(APITestCase):
    """Tests for company-scoped data isolation in purchasing views."""

    def setUp(self):
        self.company_a = CompanyFactory()
        self.company_b = CompanyFactory()
        self.user_a = User.objects.create_user(
            username="po_user_a", password="password", is_staff=True
        )
        self.user_b = User.objects.create_user(
            username="po_user_b", password="password", is_staff=True
        )
        from core.models import UserProfile

        UserProfile.objects.create(user=self.user_a, company=self.company_a, role="admin")
        UserProfile.objects.create(user=self.user_b, company=self.company_b, role="admin")

        self.warehouse_a = WarehouseFactory(company=self.company_a)
        self.warehouse_b = WarehouseFactory(company=self.company_b)

        self.po_a = PurchaseOrderFactory(warehouse=self.warehouse_a, company=self.company_a)
        self.po_b = PurchaseOrderFactory(warehouse=self.warehouse_b, company=self.company_b)

    def test_purchase_order_list_scoped_by_company(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get("/purchase-order/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [po["id"] for po in response.data["results"]]
        self.assertIn(str(self.po_a.id), ids)
        self.assertNotIn(str(self.po_b.id), ids)

    def test_replenishment_scoped_by_company(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get("/replenishment/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class CogsAllocationEdgeCaseTests(TestCase):
    """Tests for COGS allocation zero-guard edge cases (Phase Cogs Fix)."""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.inventory_service = InventoryService()

    def _build_cogs_data(self, variant, ordered_qty=10, received_qty=10, **kwargs):
        data = {
            "product_variant_id": str(variant.id),
            "ordered_qty": ordered_qty,
            "received_qty": received_qty,
            "updated_qty": 0,
            "unit_price_foreign": 10,
            "discounted_unit_price_foreign": 10,
            "discounted_total_price_base": 220000,
            "exchange_rate": 2200,
        }
        data.update(kwargs)
        return [data]

    def test_zero_total_item_amount_delivery_and_commission_zero(self):
        """Test A: zero total_item_amount -> delivery and commission allocations are 0."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
            shipping_fee=50000,
            delivery_fee=Decimal("10"),
            exchange_rate=2200,
            total_item_amount=0,
            commission_fee=10000,
        )
        data = self._build_cogs_data(
            self.product_variant,
            discounted_total_price_base=0,
        )
        self.inventory_service.update_cogs_on_po(po, PurchaseOrder.POStatus.DELIVERED, data)
        cogs = ProductCogs.objects.get(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        )
        self.assertEqual(cogs.allocated_delivery_fee, 0)
        self.assertEqual(cogs.allocated_commission_fee, 0)
        self.assertEqual(cogs.allocated_shipping_fee, 0)

    def test_zero_product_dimensions_shipping_allocation_zero(self):
        """Test B: all product dimensions are 0 -> total CBM is 0 -> shipping allocation is 0."""
        zero_dim_product = ProductFactory(
            category=self.category,
            company=self.company,
            length=0,
            width=0,
            height=0,
        )
        zero_dim_variant = ProductVariantFactory(product=zero_dim_product)
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
            shipping_fee=50000,
            delivery_fee=Decimal("0"),
            exchange_rate=2200,
            total_item_amount=220000,
            commission_fee=0,
        )
        data = self._build_cogs_data(zero_dim_variant)
        self.inventory_service.update_cogs_on_po(po, PurchaseOrder.POStatus.DELIVERED, data)
        cogs = ProductCogs.objects.get(
            product_variant=zero_dim_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        )
        self.assertEqual(cogs.allocated_shipping_fee, 0)
        unit_price_idr = Decimal("10") * Decimal("2200")
        expected_cogs = int(unit_price_idr)
        self.assertEqual(cogs.cogs_amount, expected_cogs)

    def test_zero_commission_fee_commission_allocation_zero(self):
        """Test C: commission_fee_pct=0 -> allocated_commission_fee is 0."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
            commission_fee_pct=0,
            commission_fee=0,
            delivery_fee=Decimal("5"),
            exchange_rate=2200,
            shipping_fee=30000,
            total_item_amount=220000,
        )
        data = self._build_cogs_data(self.product_variant)
        self.inventory_service.update_cogs_on_po(po, PurchaseOrder.POStatus.DELIVERED, data)
        cogs = ProductCogs.objects.get(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        )
        self.assertEqual(cogs.allocated_commission_fee, 0)
        unit_price_idr = int(Decimal("10") * Decimal("2200"))
        delivery_per_unit = int(int(Decimal("5") * Decimal("2200")) / 10)
        expected_cogs = unit_price_idr + delivery_per_unit
        self.assertEqual(cogs.cogs_amount, expected_cogs)


class ReplenishmentViewTest(TestCase):
    """Tests for the replenishment planning endpoint."""

    def setUp(self):
        from core.models import UserProfile

        self.client = APIClient()
        self.user = User.objects.create_user(
            username="replenishment_staff",
            password="testpass",
            is_staff=True,
        )
        self.client.force_authenticate(user=self.user)
        self.company = CompanyFactory()
        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)

    def test_empty_response_no_variants(self):
        """GET /replenishment/ with no active variants -> 200, results is a list."""
        response = self.client.get("/replenishment/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"results": []})

    def test_variant_soh_included(self):
        """Verify stock_on_hand reflects ProductVariantWarehouse.physical_qty."""
        variant = ProductVariantFactory(product=self.product, is_active=True)
        ProductVariantWarehouseFactory(
            product_variant=variant, warehouse=self.warehouse, physical_qty=50
        )
        response = self.client.get("/replenishment/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result = next(r for r in response.data["results"] if r["variant_id"] == str(variant.id))
        self.assertEqual(result["stock_on_hand"], 50)

    def test_incoming_qty_from_ordered_po(self):
        """incoming_qty = ordered_qty - received_qty for ORDERED POs."""
        variant = ProductVariantFactory(product=self.product, is_active=True)
        po = PurchaseOrderFactory(
            warehouse=self.warehouse, company=self.company, status=PurchaseOrder.POStatus.ORDERED
        )
        PurchaseOrderDetailFactory(
            purchase_order=po, product_variant=variant, ordered_qty=20, received_qty=5
        )
        response = self.client.get("/replenishment/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result = next(r for r in response.data["results"] if r["variant_id"] == str(variant.id))
        self.assertEqual(result["incoming_qty"], 15)

    def test_cancelled_po_excluded(self):
        """CANCELLED PO should not contribute to incoming_qty."""
        variant = ProductVariantFactory(product=self.product, is_active=True)
        po = PurchaseOrderFactory(
            warehouse=self.warehouse, company=self.company, status=PurchaseOrder.POStatus.CANCELLED
        )
        PurchaseOrderDetailFactory(
            purchase_order=po, product_variant=variant, ordered_qty=100, received_qty=0
        )
        response = self.client.get("/replenishment/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result = next(r for r in response.data["results"] if r["variant_id"] == str(variant.id))
        self.assertEqual(result["incoming_qty"], 0)

    def test_avg_sales_keys_present(self):
        """Verify avg_sales_7d and avg_sales_30d keys exist and are numeric."""
        variant = ProductVariantFactory(product=self.product, is_active=True)
        response = self.client.get("/replenishment/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result = next(r for r in response.data["results"] if r["variant_id"] == str(variant.id))
        self.assertIn("avg_sales_7d", result)
        self.assertIn("avg_sales_30d", result)
        self.assertIsInstance(result["avg_sales_7d"], float)
        self.assertIsInstance(result["avg_sales_30d"], float)


class PaginationOrderingSummaryTest(TestCase):
    """Tests for pagination, ordering, and summary endpoint on PurchaseOrder views."""

    def setUp(self):
        from core.models import UserProfile

        self.client = APIClient()
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)

        user = User.objects.create_user(username="pos_staff", password="password", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)

    def test_pagination_default_page_size_is_10(self):
        """create 15 POs, GET without page_size, assert len(results)==10 and count==15"""
        for _ in range(15):
            PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)

        response = self.client.get("/purchase-order/", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 10)
        self.assertEqual(response.data["count"], 15)

    def test_pagination_custom_page_size(self):
        """GET with page_size=5, assert len(results)==5"""
        for _ in range(15):
            PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)

        response = self.client.get("/purchase-order/?page_size=5", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 5)

    def test_ordering_by_total_amount_asc(self):
        """create 3 POs with different total_amount, GET ?ordering=total_amount, assert ascending"""
        amounts = [100000, 500000, 250000]
        for amt in amounts:
            PurchaseOrderFactory(
                warehouse=self.warehouse,
                company=self.company,
                total_amount=amt,
            )

        response = self.client.get("/purchase-order/?ordering=total_amount", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result_amounts = [item["total_amount"] for item in response.data["results"]]
        self.assertEqual(result_amounts, sorted(result_amounts))

    def test_summary_returns_upcoming_only(self):
        """create 1 ORDERED + 1 SHIPPED + 1 DELIVERED PO, GET /summary/, assert upcoming_count==2"""
        ordered_po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
            total_amount=100000,
            total_item_amount=80000,
            procure_amount=20000,
        )
        shipped_po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            total_amount=200000,
            total_item_amount=150000,
            procure_amount=50000,
        )
        PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
            total_amount=300000,
            total_item_amount=250000,
            procure_amount=50000,
        )

        response = self.client.get("/purchase-order/summary/", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["upcoming_count"], 2)
        expected_total_amount = (ordered_po.total_amount or 0) + (shipped_po.total_amount or 0)
        expected_total_item_amount = (ordered_po.total_item_amount or 0) + (
            shipped_po.total_item_amount or 0
        )
        expected_procure_amount = (ordered_po.procure_amount or 0) + (
            shipped_po.procure_amount or 0
        )
        self.assertEqual(response.data["upcoming_total_amount"], expected_total_amount)
        self.assertEqual(response.data["upcoming_total_item_amount"], expected_total_item_amount)
        self.assertEqual(response.data["upcoming_procure_amount"], expected_procure_amount)

    def test_forecast_cbm_in_list_response(self):
        """create PO with forecast_cbm set, assert it appears in list response"""
        PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            forecast_cbm=2.5,
        )

        response = self.client.get("/purchase-order/", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("forecast_cbm", response.data["results"][0])
        self.assertEqual(response.data["results"][0]["forecast_cbm"], "2.500")


class PurchaseOrderStatusHistoryTest(TestCase):
    """Tests for PurchaseOrder state machine and PurchaseOrderStatusHistory."""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)

    def test_po_model_get_next_status(self):
        """DRAFT -> ORDERED, COMPLETED -> None"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )
        self.assertEqual(po.get_next_status(), PurchaseOrder.POStatus.ORDERED)

        po.status = PurchaseOrder.POStatus.COMPLETED
        self.assertIsNone(po.get_next_status())

    def test_po_model_can_advance_to(self):
        """DRAFT can advance to ORDERED, cannot advance to SHIPPED"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )
        self.assertTrue(po.can_advance_to(PurchaseOrder.POStatus.ORDERED))
        self.assertFalse(po.can_advance_to(PurchaseOrder.POStatus.SHIPPED))

    def test_status_history_created_on_advance(self):
        """Call advance_status endpoint DRAFT -> ORDERED, assert history record."""
        from core.models import UserProfile

        client = APIClient()
        user = User.objects.create_user(username="history_test", password="password", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client.force_authenticate(user=user)

        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            purchase_order_invoice_file="test.pdf",
            invoice_number="INV-001",
            invoice_date=date.today(),
            commission_fee_pct=Decimal("10"),
            forwarder_name="Forwarder",
            supplier_name="Supplier",
            shop_services="Service",
            delivery_fee=Decimal("0"),
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=10,
            unit_price_foreign=Decimal("10"),
        )

        response = client.post(
            f"/purchase-order/{po.id}/advance_status/",
            {"status": PurchaseOrder.POStatus.ORDERED},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        history = PurchaseOrderStatusHistory.objects.filter(purchase_order=po)
        self.assertEqual(history.count(), 1)
        record = history.first()
        self.assertEqual(record.from_status, PurchaseOrder.POStatus.DRAFT)
        self.assertEqual(record.to_status, PurchaseOrder.POStatus.ORDERED)
        self.assertEqual(record.changed_by, user)

    def test_read_serializer_includes_next_status_and_history(self):
        """GET detail -> next_status and status_history in response."""
        from core.models import UserProfile

        client = APIClient()
        user = User.objects.create_user(
            username="read_serializer_test", password="password", is_staff=True
        )
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client.force_authenticate(user=user)

        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )
        PurchaseOrderDetailFactory(purchase_order=po, product_variant=self.product_variant)

        response = client.get(f"/purchase-order/{po.id}/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("next_status", response.data)
        self.assertIn("status_history", response.data)
        self.assertIsInstance(response.data["status_history"], list)
        self.assertEqual(response.data["next_status"], PurchaseOrder.POStatus.ORDERED)


class PurchaseOrderRequirementsCheckTest(TestCase):
    """Tests for check_purchase_order_requirements and check_transition endpoint."""

    def setUp(self):
        from core.models import UserProfile

        self.client = APIClient()
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.user = User.objects.create_user(
            username="req_check_user", password="password", is_staff=True
        )
        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)
        self.service = PurchaseOrderService()

    def test_check_po_requirements_ordered_missing(self):
        """Bare DRAFT PO: all ORDERED fields should be returned as missing."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )
        missing = self.service.check_purchase_order_requirements(po, PurchaseOrder.POStatus.ORDERED)

        field_names = {item["field"] for item in missing}
        self.assertIn("exchange_rate", field_names)
        self.assertIn("invoice_number", field_names)
        self.assertIn("purchase_order_invoice_file", field_names)
        self.assertIn("invoice_date", field_names)
        self.assertIn("forwarder_name", field_names)
        self.assertIn("shop_services", field_names)
        self.assertIn("delivery_fee", field_names)
        self.assertIn("order_details", field_names)

    def test_check_po_requirements_ordered_all_present(self):
        """Fully populated DRAFT PO: empty list returned."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            purchase_order_invoice_file="invoice.pdf",
            invoice_number="INV-001",
            invoice_date=date.today(),
            commission_fee_pct=Decimal("10"),
            forwarder_name="Forwarder",
            supplier_name="Supplier",
            shop_services="Service",
            delivery_fee=Decimal("0"),
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=10,
        )

        missing = self.service.check_purchase_order_requirements(po, PurchaseOrder.POStatus.ORDERED)
        self.assertEqual(missing, [])

    def test_check_po_requirements_shipped_partial(self):
        """ORDERED PO with DO number/file but no cbm/weight: only cbm and weight in missing."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
            delivery_order_number="DO-001",
            delivery_order_file="do.pdf",
        )
        missing = self.service.check_purchase_order_requirements(po, PurchaseOrder.POStatus.SHIPPED)
        missing_fields = {item["field"] for item in missing}

        self.assertIn("cbm", missing_fields)
        self.assertIn("weight", missing_fields)
        self.assertIn("shipping_fee_per_cbm", missing_fields)
        self.assertNotIn("delivery_order_number", missing_fields)
        self.assertNotIn("delivery_order_file", missing_fields)

    def test_check_transition_endpoint_missing(self):
        """POST to check_transition with SHIPPED on ORDERED PO missing cbm -> can_transition=false."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
            delivery_order_number="DO-001",
            delivery_order_file="do.pdf",
            shipping_fee_per_cbm=100,
        )

        response = self.client.post(
            f"/purchase-order/{po.id}/check_transition/",
            {"status": PurchaseOrder.POStatus.SHIPPED},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["can_transition"])
        field_names = {item["field"] for item in response.data["missing_fields"]}
        self.assertIn("cbm", field_names)
        self.assertIn("weight", field_names)

    def test_advance_status_blocked_when_fields_missing(self):
        """POST advance_status on bare DRAFT PO -> 400 with missing_fields."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )

        response = self.client.post(
            f"/purchase-order/{po.id}/advance_status/",
            {"status": PurchaseOrder.POStatus.ORDERED},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("missing_fields", response.data)


class EditableFieldsAndNoteTest(TestCase):
    """Tests for editable_fields, note, locked field enforcement, and transition warnings."""

    def setUp(self):
        from core.models import UserProfile

        self.client = APIClient()
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.user = User.objects.create_user(
            username="edit_fields_user", password="password", is_staff=True
        )
        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)
        self.service = PurchaseOrderService()

    def test_get_editable_fields_draft(self):
        """Assert exchange_rate in DRAFT editable header."""
        fields = PurchaseOrder.get_editable_fields(PurchaseOrder.POStatus.DRAFT)
        self.assertIn("exchange_rate", fields["header"])
        self.assertIn("ordered_qty", fields["order_detail"])

    def test_ordered_po_editable_fields_includes_ordered_qty(self):
        """ORDERED status allows editing ordered_qty only (not price fields)."""
        fields = PurchaseOrder.get_editable_fields(PurchaseOrder.POStatus.ORDERED)
        self.assertIn("ordered_qty", fields["order_detail"])
        self.assertNotIn("unit_price_foreign", fields["order_detail"])
        self.assertNotIn("discounted_unit_price_foreign", fields["order_detail"])

    def test_ordered_po_can_update_ordered_qty(self):
        """PATCH ordered_qty on an existing ORDERED PO detail should succeed and update the qty."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
            exchange_rate=Decimal("2250"),
            commission_fee_pct=5,
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=10,
            unit_price_foreign=Decimal("25.000"),
            discounted_unit_price_foreign=Decimal("22.000"),
        )
        # Recalculate PO totals to reflect the detail
        PurchaseOrderService()._recalculate_po_totals(po)

        response = self.client.patch(
            f"/purchase-order/{po.id}/",
            {
                "order_details": [
                    {"id": str(detail.id), "ordered_qty": 15}
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        detail.refresh_from_db()
        self.assertEqual(detail.ordered_qty, 15)

    def test_draft_po_editable_fields_has_order_detail_fields(self):
        """DRAFT status should have all editable order_detail fields."""
        fields = PurchaseOrder.get_editable_fields(PurchaseOrder.POStatus.DRAFT)
        expected = ["ordered_qty", "unit_price_foreign", "discounted_unit_price_foreign"]
        for field in expected:
            self.assertIn(field, fields["order_detail"])

    def test_get_editable_fields_shipped(self):
        """Assert exchange_rate NOT in SHIPPED; delivery_order_number IS in SHIPPED."""
        fields = PurchaseOrder.get_editable_fields(PurchaseOrder.POStatus.SHIPPED)
        self.assertNotIn("exchange_rate", fields["header"])
        self.assertIn("delivery_order_number", fields["header"])
        self.assertEqual(fields["order_detail"], [])

    def test_get_editable_fields_delivered(self):
        """Assert cbm NOT in DELIVERED; received_qty IS in DELIVERED order_detail."""
        fields = PurchaseOrder.get_editable_fields(PurchaseOrder.POStatus.DELIVERED)
        self.assertNotIn("cbm", fields["header"])
        self.assertIn("received_qty", fields["order_detail"])

    def test_get_editable_fields_completed(self):
        """Assert only note is in COMPLETED header, order_detail is empty."""
        fields = PurchaseOrder.get_editable_fields(PurchaseOrder.POStatus.COMPLETED)
        self.assertEqual(fields["header"], ["note"])
        self.assertEqual(fields["order_detail"], [])

    def test_note_field_in_list_response(self):
        """Create PO with note='test note', assert note appears in list API response."""
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company, note="test note")
        response = self.client.get("/purchase-order/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        po_data = next(r for r in response.data["results"] if r["id"] == str(po.id))
        self.assertEqual(po_data["note"], "test note")

    def test_locked_field_rejected_by_serializer(self):
        """PATCH with exchange_rate on a SHIPPED PO, assert 400 with error."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
        )
        response = self.client.patch(
            f"/purchase-order/{po.id}/",
            {"exchange_rate": 2300},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("exchange_rate", str(response.data))

    def test_transition_warnings_partial_receipt(self):
        """DELIVERED PO with partial receipt -> check_transition returns partial_receipt warning."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
            received_qty=80,
        )
        po.refresh_from_db()

        response = self.client.post(
            f"/purchase-order/{po.id}/check_transition/",
            {"status": PurchaseOrder.POStatus.COMPLETED},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        warnings = response.data.get("warnings", [])
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["type"], "partial_receipt")


    def test_snapshot_fields_populated_on_advance_to_ordered(self):
        """advancing PO to ORDERED writes avg_sales, avg_sales_7d, stock_on_hand, incoming_qty
        on each PurchaseOrderDetail."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=10,
        )

        with patch("apps.purchasing.serializers.compress_pdf_file"):
            self.service.update_purchase_order(
                po,
                {
                    "status": PurchaseOrder.POStatus.ORDERED,
                    "purchase_order_invoice_file": "invoice.pdf",
                    "invoice_number": "INV-SNAP-001",
                    "invoice_date": date(2026, 1, 15),
                    "commission_fee_pct": 5,
                    "forwarder_name": "Test Forwarder",
                    "supplier_name": "Test Supplier",
                    "shop_services": "Test Services",
                    "delivery_fee": 0,
                    "exchange_rate": 2200,
                },
            )

        detail.refresh_from_db()
        self.assertIsNotNone(detail.avg_sales)
        self.assertIsNotNone(detail.avg_sales_7d)
        self.assertEqual(float(detail.avg_sales), 0.0)
        self.assertEqual(float(detail.avg_sales_7d), 0.0)
        self.assertEqual(detail.stock_on_hand, 0)
        self.assertEqual(detail.incoming_qty, 0)

    def test_snapshot_fields_in_api_response(self):
        """avg_sales, avg_sales_7d, stock_on_hand, incoming_qty are returned in PO detail API."""
        client = APIClient()
        user = User.objects.create_user(
            username="snapshot_api_user", password="password", is_staff=True
        )
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client.force_authenticate(user=user)

        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=5,
        )
        response = client.get(f"/purchase-order/{po.id}/", format="json")
        self.assertEqual(response.status_code, 200)
        detail_data = response.data["order_details"][0]
        self.assertIn("avg_sales", detail_data)
        self.assertIn("avg_sales_7d", detail_data)
        self.assertIn("stock_on_hand", detail_data)
        self.assertIn("incoming_qty", detail_data)


class FreightCalculationTest(TestCase):
    """Tests for tiered freight (shipping_fee) formula."""

    def setUp(self):
        self.service = PurchaseOrderService()

    def test_freight_minimum_cbm(self):
        """cbm=0.05, rate=10_000_000 → 1_000_000 (0.1 × 10M)"""
        result = PurchaseOrderService._calc_shipping_fee(Decimal("10000000"), Decimal("0.05"))
        self.assertEqual(result, 1_000_000)

    def test_freight_middle_tier(self):
        """cbm=0.48, rate=10_000_000 → 4_900_000 (0.48×10M + 100_000)"""
        result = PurchaseOrderService._calc_shipping_fee(Decimal("10000000"), Decimal("0.48"))
        self.assertEqual(result, 4_900_000)

    def test_freight_standard_tier(self):
        """cbm=1.5, rate=3_000_000 → 4_500_000 (1.5×3M)"""
        result = PurchaseOrderService._calc_shipping_fee(Decimal("3000000"), Decimal("1.5"))
        self.assertEqual(result, 4_500_000)

    def test_freight_exact_boundary_01(self):
        """cbm=0.1, rate=5_000_000 → 600_000 (0.1×5M + 100_000, since 0.1 is NOT < 0.1)"""
        result = PurchaseOrderService._calc_shipping_fee(Decimal("5000000"), Decimal("0.1"))
        self.assertEqual(result, 600_000)

    def test_freight_exact_boundary_05(self):
        """cbm=0.5, rate=5_000_000 → 2_500_000 (0.5×5M, since 0.5 is NOT < 0.5)"""
        result = PurchaseOrderService._calc_shipping_fee(Decimal("5000000"), Decimal("0.5"))
        self.assertEqual(result, 2_500_000)

    def test_freight_zero_cbm(self):
        """cbm=0 → 0"""
        result = PurchaseOrderService._calc_shipping_fee(Decimal("5000000"), Decimal("0"))
        self.assertEqual(result, 0)


class CreatePOFixTest(TestCase):
    """Tests for PO create endpoint fixes — relaxed validation, company_id injection, id return."""

    def setUp(self):
        from core.models import UserProfile

        self.client = APIClient()
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.user = User.objects.create_user(
            username="create_po_fix_user", password="password", is_staff=True
        )
        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_create_po_minimal_draft(self):
        """POST with only warehouse_id and one order_detail should succeed (201)."""
        payload = {
            "warehouse_id": str(self.warehouse.id),
            "order_details": [
                {
                    "product_variant_id": str(self.product_variant.id),
                    "ordered_qty": 10,
                    "unit_price_foreign": 50,
                }
            ],
        }

        response = self.client.post("/purchase-order/", payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("id", response.data)

    def test_create_po_returns_id(self):
        """Response body contains an 'id' that matches a real PurchaseOrder."""
        payload = {
            "warehouse_id": str(self.warehouse.id),
            "order_details": [
                {
                    "product_variant_id": str(self.product_variant.id),
                    "ordered_qty": 5,
                    "unit_price_foreign": 20,
                }
            ],
        }

        response = self.client.post("/purchase-order/", payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        po_id = response.data.get("id")
        self.assertIsNotNone(po_id)
        self.assertTrue(PurchaseOrder.objects.filter(id=po_id).exists())

    def test_create_po_injects_company_id(self):
        """PO created without company_id in payload should use the authenticated user's company."""
        payload = {
            "warehouse_id": str(self.warehouse.id),
            "order_details": [
                {
                    "product_variant_id": str(self.product_variant.id),
                    "ordered_qty": 3,
                    "unit_price_foreign": 100,
                }
            ],
        }

        response = self.client.post("/purchase-order/", payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        po = PurchaseOrder.objects.get(id=response.data["id"])
        self.assertEqual(po.company, self.company)


class PurchaseOrderDetailSerializerProductFieldsTest(TestCase):
    """Tests for product_id, product_name, product_supplier_link in PurchaseOrderDetailSerializer."""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(
            company=self.company,
            category=self.category,
            supplier_link="https://example.com/supplier/product-789",
        )
        self.product_variant = ProductVariantFactory(product=self.product)
        self.po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        self.detail = PurchaseOrderDetailFactory(
            purchase_order=self.po, product_variant=self.product_variant
        )

    def test_po_detail_serializer_includes_product_fields(self):
        from apps.purchasing.serializers import PurchaseOrderDetailSerializer

        serializer = PurchaseOrderDetailSerializer(self.detail)
        self.assertIn("product_id", serializer.data)
        self.assertIn("product_name", serializer.data)
        self.assertIn("product_supplier_link", serializer.data)
        self.assertEqual(serializer.data["product_id"], str(self.product.id))
        self.assertEqual(serializer.data["product_name"], self.product.name)
        self.assertEqual(
            serializer.data["product_supplier_link"], "https://example.com/supplier/product-789"
        )


class ForecastShippingPerCbmTest(TestCase):
    """Tests for forecast_shipping_fee_per_cbm auto-calculation on PurchaseOrder."""

    def setUp(self):
        from core.models import UserProfile

        self.client = APIClient()
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.user = User.objects.create_user(
            username="forecast_per_cbm_test_user", password="password", is_staff=True
        )
        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_forecast_shipping_fee_auto_calculated(self):
        """PATCH a DRAFT PO with forecast_cbm and forecast_shipping_fee_per_cbm should auto-calculate forecast_shipping_fee."""
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        payload = {
            "forecast_cbm": "2.500",
            "forecast_shipping_fee_per_cbm": 2000000,
        }
        response = self.client.patch(f"/purchase-order/{po.id}/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        po.refresh_from_db()
        self.assertEqual(po.forecast_shipping_fee, 5000000)
        self.assertEqual(po.forecast_shipping_fee_per_cbm, 2000000)

    def test_po_detail_serializer_includes_product_photo_url(self):
        """Serialize a PurchaseOrderDetail, assert product_photo_url key exists."""
        from apps.purchasing.serializers import PurchaseOrderDetailSerializer

        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        detail = PurchaseOrderDetailFactory(
            purchase_order=po, product_variant=self.product_variant
        )
        serializer = PurchaseOrderDetailSerializer(detail)
        self.assertIn("product_photo_url", serializer.data)


class ExchangeRateRecalculationTest(TestCase):
    """Tests for recalculating item base prices when exchange_rate changes."""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.service = PurchaseOrderService()

    def test_recalculates_item_prices_when_exchange_rate_set(self):
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            exchange_rate=None,
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            unit_price_foreign=Decimal("100"),
            ordered_qty=2,
            unit_price_base=None,
            total_price_base=None,
            discounted_unit_price_base=None,
            discounted_total_price_base=None,
        )
        po.refresh_from_db()

        self.service.update_purchase_order(po, {"exchange_rate": Decimal("2000")})

        detail.refresh_from_db()
        self.assertEqual(detail.unit_price_base, 200000)
        self.assertEqual(detail.total_price_base, 400000)
        self.assertEqual(detail.discounted_unit_price_base, 200000)
        self.assertEqual(detail.discounted_total_price_base, 400000)

    def test_recalculates_item_prices_when_exchange_rate_updated(self):
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            exchange_rate=Decimal("1000"),
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            unit_price_foreign=Decimal("50"),
            ordered_qty=3,
            unit_price_base=50000,
            total_price_base=150000,
            discounted_unit_price_base=50000,
            discounted_total_price_base=150000,
        )
        po.refresh_from_db()

        self.service.update_purchase_order(po, {"exchange_rate": Decimal("2000")})

        detail.refresh_from_db()
        self.assertEqual(detail.total_price_base, 300000)

    def test_does_not_recalculate_when_exchange_rate_not_in_update(self):
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            exchange_rate=Decimal("1000"),
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            unit_price_foreign=Decimal("50"),
            ordered_qty=3,
            unit_price_base=50000,
            total_price_base=150000,
            discounted_unit_price_base=50000,
            discounted_total_price_base=150000,
        )
        po.refresh_from_db()

        self.service.update_purchase_order(po, {"supplier_name": "New Supplier"})

        detail.refresh_from_db()
        self.assertEqual(detail.unit_price_base, 50000)
        self.assertEqual(detail.total_price_base, 150000)


class ForecastCbmAutoCalculationTest(TestCase):
    """Tests for auto-calculation of forecast_cbm from product dimensions."""

    def setUp(self):
        from core.models import UserProfile

        self.client = APIClient()
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.user = User.objects.create_user(
            username="cbm_auto_test_user", password="password", is_staff=True
        )
        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_forecast_cbm_auto_calculated_on_item_add(self):
        product = ProductFactory(
            category=self.category, company=self.company, length=25, width=20, height=10
        )
        product_variant = ProductVariantFactory(product=product)
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)

        response = self.client.patch(
            f"/purchase-order/{po.id}/",
            {
                "order_details": [
                    {
                        "product_variant_id": str(product_variant.id),
                        "ordered_qty": 10,
                        "unit_price_foreign": 10,
                    }
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        po.refresh_from_db()
        self.assertEqual(po.forecast_cbm, Decimal("0.050000"))

    def test_forecast_cbm_not_set_when_no_dimensions(self):
        product = ProductFactory(
            category=self.category, company=self.company, length=0, width=0, height=0
        )
        product_variant = ProductVariantFactory(product=product)
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)

        response = self.client.patch(
            f"/purchase-order/{po.id}/",
            {
                "order_details": [
                    {
                        "product_variant_id": str(product_variant.id),
                        "ordered_qty": 10,
                        "unit_price_foreign": 10,
                    }
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        po.refresh_from_db()
        self.assertIsNone(po.forecast_cbm)

    def test_forecast_cbm_sums_multiple_items(self):
        product_a = ProductFactory(
            category=self.category, company=self.company, length=10, width=10, height=10
        )
        variant_a = ProductVariantFactory(product=product_a)
        product_b = ProductFactory(
            category=self.category, company=self.company, length=20, width=20, height=5
        )
        variant_b = ProductVariantFactory(product=product_b)
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)

        response = self.client.patch(
            f"/purchase-order/{po.id}/",
            {
                "order_details": [
                    {
                        "product_variant_id": str(variant_a.id),
                        "ordered_qty": 5,
                        "unit_price_foreign": 10,
                    },
                    {
                        "product_variant_id": str(variant_b.id),
                        "ordered_qty": 3,
                        "unit_price_foreign": 20,
                    },
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        po.refresh_from_db()
        self.assertEqual(po.forecast_cbm, Decimal("0.011000"))
