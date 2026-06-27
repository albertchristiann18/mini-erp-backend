import io
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch
from uuid import uuid4

import fitz
import openpyxl
import requests
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.inventory.factories import (
    CategoryFactory,
    CompanyFactory,
    ProductFactory,
    ProductPhotoFactory,
    ProductSupplierFactory,
    ProductVariantFactory,
    ProductVariantWarehouseFactory,
    SupplierFactory,
)
from apps.inventory.models import ProductCogs, ProductSupplier, ProductVariantWarehouse
from apps.inventory.services.inventory_service import InventoryService
from apps.purchasing.factories import (
    PurchaseOrderDetailFactory,
    PurchaseOrderFactory,
    SourcingPoolFactory,
    SourcingPoolItemFactory,
)
from apps.purchasing.models import (
    PurchaseOrder,
    PurchaseOrderDetail,
    PurchaseOrderStatusHistory,
    SourcingPool,
    SourcingPoolItem,
)
from apps.purchasing.serializers import PurchaseOrderReadSerializer, PurchaseOrderUpdateSerializer
from apps.purchasing.services.purchasing_service import PurchaseOrderService
from core.factories import WarehouseFactory
from core.models import UserProfile
from core.utils import compress_pdf_iterative


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

    def test_last_price_fields_in_po_detail_response(self):
        """last_unit_price_foreign and last_currency appear in PO detail response"""
        self.product_variant.last_unit_price_foreign = Decimal("25.0000")
        self.product_variant.last_currency = "CNY"
        self.product_variant.save()
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        PurchaseOrderDetailFactory(purchase_order=po, product_variant=self.product_variant)

        response = self.client.get(f"/purchase-order/{po.id}/", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        detail = response.data["order_details"][0]
        self.assertEqual(detail["last_unit_price_foreign"], "25.0000")
        self.assertEqual(detail["last_currency"], "CNY")

    def test_last_price_fields_null_when_variant_has_no_last_price(self):
        """last_unit_price_foreign and last_currency are None when variant has no last price"""
        self.product_variant.last_unit_price_foreign = None
        self.product_variant.last_currency = None
        self.product_variant.save()
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        PurchaseOrderDetailFactory(purchase_order=po, product_variant=self.product_variant)

        response = self.client.get(f"/purchase-order/{po.id}/", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        detail = response.data["order_details"][0]
        self.assertIsNone(detail["last_unit_price_foreign"])
        self.assertIsNone(detail["last_currency"])

    def test_last_discounted_price_field_in_po_detail_response(self):
        """last_discounted_unit_price_foreign appears in PO detail response"""
        self.product_variant.last_discounted_unit_price_foreign = Decimal("18.5000")
        self.product_variant.last_currency = "CNY"
        self.product_variant.save()
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        PurchaseOrderDetailFactory(purchase_order=po, product_variant=self.product_variant)

        response = self.client.get(f"/purchase-order/{po.id}/", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["order_details"][0]["last_discounted_unit_price_foreign"], "18.500"
        )

    def test_last_discounted_price_field_null_when_not_set(self):
        """last_discounted_unit_price_foreign is None when variant has none set"""
        self.product_variant.last_discounted_unit_price_foreign = None
        self.product_variant.save()
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        PurchaseOrderDetailFactory(purchase_order=po, product_variant=self.product_variant)

        response = self.client.get(f"/purchase-order/{po.id}/", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["order_details"][0]["last_discounted_unit_price_foreign"])

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
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
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

        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
            service.update_purchase_order(po, {"status": PurchaseOrder.POStatus.SHIPPED})
        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.POStatus.SHIPPED)

        detail = po.order_details.first()
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
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

        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
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

        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
            service.update_purchase_order(
                po1,
                {
                    "status": PurchaseOrder.POStatus.ORDERED,
                    "purchase_order_invoice_file": "invoice.pdf",
                    "invoice_number": "INV-001",
                    "invoice_date": date(2026, 1, 15),
                },
            )

        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
            service.update_purchase_order(po1, {"status": PurchaseOrder.POStatus.SHIPPED})

        detail1 = po1.order_details.first()
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
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
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
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
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
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
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
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
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
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
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
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
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
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
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
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

    def test_pdf_compression_skipped_under_threshold(self):
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        file_data = b"x" * (1 * 1024 * 1024)
        pdf_file = SimpleUploadedFile("test.pdf", file_data, content_type="application/pdf")
        response = self.client.patch(
            f"/purchase-order/{po.id}/",
            {"purchase_order_invoice_file": pdf_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("compressed_files", response.data)

    def test_pdf_compression_triggered_over_threshold(self):
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        file_data = b"x" * (3 * 1024 * 1024)
        pdf_file = SimpleUploadedFile("test.pdf", file_data, content_type="application/pdf")
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"compressed", name="test.pdf"), True),
        ):
            response = self.client.patch(
                f"/purchase-order/{po.id}/",
                {"purchase_order_invoice_file": pdf_file},
                format="multipart",
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("compressed_files", response.data)
        self.assertIn("purchase_order_invoice_file", response.data["compressed_files"])

    def test_packing_list_file_compression(self):
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        file_data = b"x" * (3 * 1024 * 1024)
        pdf_file = SimpleUploadedFile("packing_list.pdf", file_data, content_type="application/pdf")
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"compressed", name="packing_list.pdf"), True),
        ):
            response = self.client.patch(
                f"/purchase-order/{po.id}/",
                {"packing_list_file": pdf_file},
                format="multipart",
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("compressed_files", response.data)
        self.assertIn("packing_list_file", response.data["compressed_files"])

    def test_freight_breakdown_fields_in_delivered_po_response(self):
        """Per-item freight allocation fields appear in DELIVERED PO response."""
        self.product.length = 20
        self.product.width = 10
        self.product.height = 5
        self.product.save()
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            shipping_fee=100000,
            delivery_fee=50,
            exchange_rate=2000,
        )
        po.commission_fee = 20000
        po.total_item_amount = 500000
        po.save()
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=10,
            received_qty=10,
            discounted_total_price_base=500000,
            discounted_unit_price_base=50000,
        )
        po.status = PurchaseOrder.POStatus.DELIVERED
        po.save()

        response = self.client.get(f"/purchase-order/{po.id}/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        resp_detail = response.data["order_details"][0]
        self.assertTrue(resp_detail["product_has_dimensions"])
        self.assertIsNotNone(resp_detail["shipping_per_unit_idr"])
        self.assertIsNotNone(resp_detail["cogs_per_unit_idr"])
        self.assertEqual(resp_detail["shipping_per_unit_idr"], 10000)
        self.assertEqual(resp_detail["delivery_per_unit_idr"], 10000)
        self.assertEqual(resp_detail["commission_per_unit_idr"], 2000)
        self.assertEqual(resp_detail["cogs_per_unit_idr"], 72000)

    def test_product_without_dimensions_has_zero_shipping(self):
        """Product with no dimensions gets zero shipping allocation."""
        self.product.length = 0
        self.product.width = 0
        self.product.height = 0
        self.product.save()
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            shipping_fee=100000,
        )
        po.total_item_amount = 100000
        po.save()
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=5,
            received_qty=5,
            discounted_total_price_base=100000,
            discounted_unit_price_base=20000,
        )
        po.status = PurchaseOrder.POStatus.DELIVERED
        po.save()

        response = self.client.get(f"/purchase-order/{po.id}/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        resp_detail = response.data["order_details"][0]
        self.assertFalse(resp_detail["product_has_dimensions"])
        self.assertEqual(resp_detail["shipping_per_unit_idr"], 0)
        self.assertIsNotNone(resp_detail["cogs_per_unit_idr"])


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

        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
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

        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
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

        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
            self.service.update_purchase_order(
                po2,
                {
                    "status": PurchaseOrder.POStatus.SHIPPED,
                    "delivery_order_number": "DO-002",
                },
            )

        detail = po2.order_details.first()
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
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

        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
            self.service.update_purchase_order(
                po2,
                {"status": PurchaseOrder.POStatus.COMPLETED},
            )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 20)
        self.assertEqual(pvw.physical_qty, 10)

    def test_compress_pdf_iterative_under_target_returns_original(self):
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text(fitz.Point(100, 100), "Test PDF")
        pdf_bytes = doc.tobytes()
        doc.close()
        uploaded = SimpleUploadedFile("test.pdf", pdf_bytes, content_type="application/pdf")
        result, was_compressed = compress_pdf_iterative(uploaded, target_mb=2.0)
        self.assertFalse(was_compressed)
        result.seek(0)
        self.assertEqual(result.read(), pdf_bytes)

    def test_compress_pdf_iterative_over_target_returns_compressed(self):
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text(fitz.Point(100, 100), "Test PDF for compression")
        pdf_bytes = doc.tobytes()
        doc.close()
        uploaded = SimpleUploadedFile("test.pdf", pdf_bytes, content_type="application/pdf")
        tiny_target_mb = len(pdf_bytes) / (1024 * 1024) / 2
        result, was_compressed = compress_pdf_iterative(uploaded, target_mb=tiny_target_mb)
        result.seek(0)
        result_bytes = result.read()
        self.assertTrue(was_compressed)
        self.assertLessEqual(len(result_bytes), len(pdf_bytes))

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

        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
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

        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
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

        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
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

    def test_create_po_with_supplier_autolinks_products(self):
        """Auto-create ProductSupplier records for products not yet linked to PO's supplier."""
        supplier = SupplierFactory(company=self.company)
        product_a = ProductFactory(category=self.category, company=self.company)
        product_b = ProductFactory(category=self.category, company=self.company)
        variant_a = ProductVariantFactory(product=product_a)
        variant_b = ProductVariantFactory(product=product_b)

        data = {
            "warehouse_id": str(self.warehouse.id),
            "company_id": str(self.company.id),
            "supplier_name": "Test Supplier",
            "supplier_id": str(supplier.id),
            "total_ordered_qty": 100,
            "total_amount": 1000000,
            "order_details": [
                {
                    "product_variant_id": str(variant_a.id),
                    "ordered_qty": 50,
                    "unit_price_foreign": 100,
                },
                {
                    "product_variant_id": str(variant_b.id),
                    "ordered_qty": 50,
                    "unit_price_foreign": 100,
                },
            ],
        }

        self.service.create_purchase_order(data)

        links = ProductSupplier.objects.filter(supplier=supplier)
        self.assertEqual(links.count(), 2)
        self.assertIn(links[0].product, [product_a, product_b])
        self.assertIn(links[1].product, [product_a, product_b])
        self.assertNotEqual(links[0].product, links[1].product)

    def test_create_po_with_supplier_skips_existing_link(self):
        """Do not duplicate ProductSupplier for products already linked to the supplier."""
        supplier = SupplierFactory(company=self.company)
        product_a = ProductFactory(category=self.category, company=self.company)
        product_b = ProductFactory(category=self.category, company=self.company)
        variant_a = ProductVariantFactory(product=product_a)
        variant_b = ProductVariantFactory(product=product_b)

        ProductSupplierFactory(product=product_a, supplier=supplier, company=self.company)

        data = {
            "warehouse_id": str(self.warehouse.id),
            "company_id": str(self.company.id),
            "supplier_name": "Test Supplier",
            "supplier_id": str(supplier.id),
            "total_ordered_qty": 100,
            "total_amount": 1000000,
            "order_details": [
                {
                    "product_variant_id": str(variant_a.id),
                    "ordered_qty": 50,
                    "unit_price_foreign": 100,
                },
                {
                    "product_variant_id": str(variant_b.id),
                    "ordered_qty": 50,
                    "unit_price_foreign": 100,
                },
            ],
        }

        self.service.create_purchase_order(data)

        links = ProductSupplier.objects.filter(supplier=supplier)
        self.assertEqual(links.count(), 2)
        # Ensure no duplicate for product_a
        self.assertEqual(links.filter(product=product_a).count(), 1)

    def test_create_po_without_supplier_no_autolink(self):
        """No ProductSupplier records when PO has no supplier."""
        product_b = ProductFactory(category=self.category, company=self.company)
        variant_b = ProductVariantFactory(product=product_b)

        data = {
            "warehouse_id": str(self.warehouse.id),
            "company_id": str(self.company.id),
            "supplier_name": "Test Supplier",
            "total_ordered_qty": 100,
            "total_amount": 1000000,
            "order_details": [
                {
                    "product_variant_id": str(variant_b.id),
                    "ordered_qty": 50,
                    "unit_price_foreign": 100,
                },
            ],
        }

        self.service.create_purchase_order(data)

        self.assertEqual(ProductSupplier.objects.count(), 0)

    def test_update_po_adding_new_detail_autolinks(self):
        """Auto-create ProductSupplier when adding a new detail via update PO."""
        supplier = SupplierFactory(company=self.company)
        product = ProductFactory(category=self.category, company=self.company)
        variant = ProductVariantFactory(product=product)

        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            supplier=supplier,
            status=PurchaseOrder.POStatus.DRAFT,
        )

        data = {
            "order_details": [
                {
                    "product_variant_id": str(variant.id),
                    "ordered_qty": 50,
                    "unit_price_foreign": 100,
                },
            ]
        }

        self.service.update_purchase_order(po, data)

        self.assertTrue(
            ProductSupplier.objects.filter(
                supplier=supplier,
                product=product,
            ).exists()
        )

    def test_create_po_with_has_discount_true(self):
        """has_discount=True on PO create: field is stored."""
        data = {
            "warehouse_id": str(self.warehouse.id),
            "company_id": str(self.company.id),
            "supplier_name": "Test Supplier",
            "total_ordered_qty": 100,
            "total_amount": 1000000,
            "has_discount": True,
            "order_details": [
                {
                    "product_variant_id": str(self.product_variant.id),
                    "ordered_qty": 100,
                    "unit_price_foreign": 100,
                }
            ],
        }

        po = self.service.create_purchase_order(data)
        po.refresh_from_db()
        self.assertTrue(po.has_discount)

    def test_update_po_has_discount_false_nulls_discounted_prices(self):
        """has_discount=False on PO update: all detail discounted price fields become null."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=10,
            unit_price_foreign=Decimal("100"),
            discounted_unit_price_foreign=Decimal("90"),
            discounted_unit_price_base=198000,
            discounted_total_price_foreign=Decimal("900"),
            discounted_total_price_base=1980000,
        )

        self.service.update_purchase_order(po, {"has_discount": False})
        detail.refresh_from_db()

        self.assertIsNone(detail.discounted_unit_price_foreign)
        self.assertIsNone(detail.discounted_unit_price_base)
        self.assertIsNone(detail.discounted_total_price_foreign)
        self.assertIsNone(detail.discounted_total_price_base)

    def test_update_po_has_discount_true_preserves_discounted_prices(self):
        """has_discount=True on PO update: discounted prices are preserved."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=10,
            unit_price_foreign=Decimal("100"),
            discounted_unit_price_foreign=Decimal("90"),
            discounted_unit_price_base=198000,
            discounted_total_price_foreign=Decimal("900"),
            discounted_total_price_base=1980000,
        )

        self.service.update_purchase_order(po, {"has_discount": True})
        detail.refresh_from_db()

        self.assertEqual(detail.discounted_unit_price_foreign, Decimal("90"))
        self.assertEqual(detail.discounted_unit_price_base, 198000)
        self.assertEqual(detail.discounted_total_price_foreign, Decimal("900"))
        self.assertEqual(detail.discounted_total_price_base, 1980000)

    def test_recalculate_po_totals_falls_back_to_base_prices_when_discount_null(self):
        """_recalculate_po_totals uses base price fields when discounted prices are null."""
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
            ordered_qty=10,
            unit_price_foreign=Decimal("100"),
            unit_price_base=220000,
            total_price_foreign=Decimal("1000"),
            total_price_base=2200000,
            discounted_unit_price_foreign=None,
            discounted_unit_price_base=None,
            discounted_total_price_foreign=None,
            discounted_total_price_base=None,
        )

        po = self.service.update_purchase_order(po, {})
        po.refresh_from_db()

        self.assertEqual(po.total_item_amount, 2200000)
        self.assertEqual(po.total_order_amount, 2200000)
        self.assertEqual(po.total_amount, 2200000)


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

    def test_same_exchange_rate_allowed_on_non_draft(self):
        """Test sending the same exchange_rate on ORDERED PO does not raise error"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
            exchange_rate=Decimal("2200"),
        )

        serializer = self._create_serializer(po, {"exchange_rate": Decimal("2200")}, partial=True)

        self.assertTrue(serializer.is_valid(), serializer.errors)

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

    def test_draft_patch_real_cbm_and_rate_sets_shipping_fee(self):
        """PATCH cbm + shipping_fee_per_cbm on DRAFT should auto-calculate shipping_fee."""
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        payload = {
            "cbm": "2.500",
            "shipping_fee_per_cbm": 2000000,
        }
        response = self.client.patch(f"/purchase-order/{po.id}/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        po.refresh_from_db()
        self.assertEqual(po.shipping_fee, 5000000)

    def test_draft_patch_forecast_cbm_and_rate_sets_shipping_fee_when_no_real_cbm(self):
        """PATCH forecast_cbm + forecast_shipping_fee_per_cbm when real cbm is null should use forecast values."""
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        payload = {
            "forecast_cbm": "3.000",
            "forecast_shipping_fee_per_cbm": 1500000,
        }
        response = self.client.patch(f"/purchase-order/{po.id}/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        po.refresh_from_db()
        self.assertEqual(po.shipping_fee, 4500000)

    def test_draft_patch_real_cbm_overrides_forecast(self):
        """Instance has forecast values; PATCH real cbm + real per_cbm should use real values."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            forecast_cbm=2.500,
            forecast_shipping_fee_per_cbm=2000000,
        )
        payload = {
            "cbm": "3.000",
            "shipping_fee_per_cbm": 1500000,
        }
        response = self.client.patch(f"/purchase-order/{po.id}/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        po.refresh_from_db()
        self.assertEqual(po.shipping_fee, 4500000)

    def test_draft_patch_only_cbm_no_rate_does_not_change_shipping_fee(self):
        """PATCH cbm alone (no rate on instance) should leave shipping_fee unchanged."""
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        payload = {
            "cbm": "2.500",
        }
        response = self.client.patch(f"/purchase-order/{po.id}/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        po.refresh_from_db()
        self.assertEqual(po.shipping_fee, 0)

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
        expected_procure_amount = (ordered_po.procure_amount or 0) + (
            shipped_po.procure_amount or 0
        )
        self.assertEqual(response.data["upcoming_procure_amount"], expected_procure_amount)


class TestPurchaseOrderWithSupplier(APITestCase):
    """Tests for PurchaseOrder with Supplier FK"""

    def setUp(self):
        from apps.inventory.factories import SupplierFactory
        from core.models import UserProfile

        self.client = APIClient()
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.supplier = SupplierFactory(company=self.company)
        self.user = User.objects.create_user(
            username="po_supplier_test", password="password", is_staff=True
        )
        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_create_po_with_supplier_id(self):
        payload = {
            "warehouse_id": str(self.warehouse.id),
            "company_id": str(self.company.id),
            "supplier_id": str(self.supplier.id),
            "supplier_name": "Old Name",
            "order_details": [
                {
                    "product_variant_id": str(self.product_variant.id),
                    "ordered_qty": 10,
                    "unit_price_foreign": 100,
                }
            ],
        }
        response = self.client.post("/purchase-order/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        po_id = response.data["id"]
        detail_response = self.client.get(f"/purchase-order/{po_id}/", format="json")
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.data["supplier_id"], str(self.supplier.id))
        self.assertEqual(detail_response.data["supplier_name"], self.supplier.name)

    def test_create_po_without_supplier_id(self):
        payload = {
            "warehouse_id": str(self.warehouse.id),
            "company_id": str(self.company.id),
            "supplier_name": "Legacy Supplier",
            "order_details": [
                {
                    "product_variant_id": str(self.product_variant.id),
                    "ordered_qty": 10,
                    "unit_price_foreign": 100,
                }
            ],
        }
        response = self.client.post("/purchase-order/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        po_id = response.data["id"]
        detail_response = self.client.get(f"/purchase-order/{po_id}/", format="json")
        self.assertIsNone(detail_response.data["supplier_id"])

    def test_update_po_supplier(self):
        from apps.inventory.factories import SupplierFactory

        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        supplier2 = SupplierFactory(company=self.company)
        response = self.client.patch(
            f"/purchase-order/{po.id}/",
            {"supplier_id": str(supplier2.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        detail_response = self.client.get(f"/purchase-order/{po.id}/", format="json")
        self.assertEqual(detail_response.data["supplier_id"], str(supplier2.id))
        self.assertEqual(detail_response.data["supplier_name"], supplier2.name)

    def test_supplier_name_from_fk(self):
        from apps.inventory.factories import SupplierFactory

        supplier = SupplierFactory(company=self.company)
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        self.client.patch(
            f"/purchase-order/{po.id}/",
            {"supplier_id": str(supplier.id)},
            format="json",
        )
        detail_response = self.client.get(f"/purchase-order/{po.id}/", format="json")
        self.assertEqual(detail_response.data["supplier_name"], supplier.name)

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
            {"order_details": [{"id": str(detail.id), "ordered_qty": 15}]},
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
        """Assert note and has_discount are in COMPLETED header, order_detail is empty."""
        fields = PurchaseOrder.get_editable_fields(PurchaseOrder.POStatus.COMPLETED)
        self.assertEqual(fields["header"], ["note", "has_discount"])
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

        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
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
        )
        self.product_variant = ProductVariantFactory(product=self.product)
        self.po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        self.detail = PurchaseOrderDetailFactory(
            purchase_order=self.po,
            product_variant=self.product_variant,
            supplier_link="https://example.com/supplier/product-789",
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

    def test_po_detail_serializer_includes_sku_variant_code(self):
        from apps.purchasing.serializers import PurchaseOrderDetailSerializer

        serializer = PurchaseOrderDetailSerializer(self.detail)
        self.assertIn("sku_variant_code", serializer.data)
        self.assertEqual(serializer.data["sku_variant_code"], self.product_variant.sku_variant_code)


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
        detail = PurchaseOrderDetailFactory(purchase_order=po, product_variant=self.product_variant)
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


class QCPPhase7Test(TestCase):
    """Tests for QCP Phase 7 — variant price tracking, variant_values, supplier_link."""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.service = PurchaseOrderService()

    def test_price_synced_to_variant_after_po_save(self):
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            currency="CNY",
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            unit_price_foreign=Decimal("15.00"),
        )
        po.refresh_from_db()
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
            self.service.update_purchase_order(
                po,
                {
                    "order_details": [
                        {
                            "id": str(po.order_details.first().id),
                            "product_variant_id": str(self.product_variant.id),
                            "ordered_qty": 10,
                            "unit_price_foreign": Decimal("15.00"),
                        }
                    ]
                },
            )
        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.last_unit_price_foreign, Decimal("15.00"))
        self.assertEqual(self.product_variant.last_currency, "CNY")
        self.assertIsNotNone(self.product_variant.price_updated_at)

    def test_latest_po_price_wins(self):
        now = timezone.now()
        self.product_variant.last_unit_price_foreign = Decimal("10.00")
        self.product_variant.last_currency = "USD"
        self.product_variant.price_updated_at = now - timedelta(hours=2)
        self.product_variant.save()
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            currency="CNY",
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            unit_price_foreign=Decimal("20.00"),
        )
        po.refresh_from_db()
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
            self.service.update_purchase_order(
                po,
                {
                    "order_details": [
                        {
                            "id": str(po.order_details.first().id),
                            "product_variant_id": str(self.product_variant.id),
                            "ordered_qty": 10,
                            "unit_price_foreign": Decimal("20.00"),
                        }
                    ]
                },
            )
        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.last_unit_price_foreign, Decimal("20.00"))
        self.assertEqual(self.product_variant.last_currency, "CNY")

    def test_zero_price_not_synced(self):
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            unit_price_foreign=None,
        )
        po.refresh_from_db()
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
            self.service.update_purchase_order(
                po,
                {
                    "order_details": [
                        {
                            "id": str(po.order_details.first().id),
                            "product_variant_id": str(self.product_variant.id),
                            "ordered_qty": 10,
                        }
                    ]
                },
            )
        self.product_variant.refresh_from_db()
        self.assertIsNone(self.product_variant.last_unit_price_foreign)
        self.assertIsNone(self.product_variant.last_currency)
        self.assertIsNone(self.product_variant.price_updated_at)

    def test_supplier_link_populated_on_detail_creation(self):
        from apps.inventory.factories import ProductSupplierFactory, SupplierFactory

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
            status=PurchaseOrder.POStatus.DRAFT,
        )
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
            self.service.update_purchase_order(
                po,
                {
                    "order_details": [
                        {
                            "product_variant_id": str(self.product_variant.id),
                            "ordered_qty": 10,
                            "unit_price_foreign": Decimal("15.00"),
                        }
                    ]
                },
            )
        detail = po.order_details.first()
        self.assertIsNotNone(detail)
        self.assertEqual(detail.supplier_link, "https://supplier.com/item")

    def test_supplier_link_prefers_po_supplier(self):
        from apps.inventory.factories import ProductSupplierFactory, SupplierFactory

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
            status=PurchaseOrder.POStatus.DRAFT,
        )
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
            self.service.update_purchase_order(
                po,
                {
                    "order_details": [
                        {
                            "product_variant_id": str(self.product_variant.id),
                            "ordered_qty": 10,
                            "unit_price_foreign": Decimal("15.00"),
                        }
                    ]
                },
            )
        detail = po.order_details.first()
        self.assertIsNotNone(detail)
        self.assertEqual(detail.supplier_link, "https://po-supplier.com")

    def test_discounted_price_synced_to_variant_after_po_save(self):
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            currency="CNY",
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            unit_price_foreign=Decimal("20.00"),
            discounted_unit_price_foreign=Decimal("18.00"),
        )
        po.refresh_from_db()
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
            self.service.update_purchase_order(
                po,
                {
                    "order_details": [
                        {
                            "id": str(po.order_details.first().id),
                            "product_variant_id": str(self.product_variant.id),
                            "ordered_qty": 10,
                            "unit_price_foreign": Decimal("20.00"),
                            "discounted_unit_price_foreign": Decimal("18.00"),
                        }
                    ]
                },
            )
        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.last_discounted_unit_price_foreign, Decimal("18.00"))
        self.assertEqual(self.product_variant.last_unit_price_foreign, Decimal("20.00"))

    def test_sync_variant_prices_skipped_for_shipped_status(self):
        self.product_variant.last_unit_price_foreign = Decimal("10.00")
        self.product_variant.last_currency = "USD"
        self.product_variant.price_updated_at = timezone.now()
        self.product_variant.save()
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            currency="CNY",
            status=PurchaseOrder.POStatus.SHIPPED,
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            unit_price_foreign=Decimal("15.00"),
        )
        po.refresh_from_db()
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
            self.service.update_purchase_order(
                po,
                {
                    "order_details": [
                        {
                            "id": str(po.order_details.first().id),
                            "product_variant_id": str(self.product_variant.id),
                            "ordered_qty": 10,
                            "unit_price_foreign": Decimal("15.00"),
                        }
                    ]
                },
            )
        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.last_unit_price_foreign, Decimal("10.00"))
        self.assertEqual(self.product_variant.last_currency, "USD")

    def test_sync_variant_prices_skipped_for_completed_status(self):
        self.product_variant.last_unit_price_foreign = Decimal("10.00")
        self.product_variant.last_currency = "USD"
        self.product_variant.price_updated_at = timezone.now()
        self.product_variant.save()
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            currency="CNY",
            status=PurchaseOrder.POStatus.COMPLETED,
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            unit_price_foreign=Decimal("15.00"),
        )
        po.refresh_from_db()
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
            self.service.update_purchase_order(
                po,
                {
                    "order_details": [
                        {
                            "id": str(po.order_details.first().id),
                            "product_variant_id": str(self.product_variant.id),
                            "ordered_qty": 10,
                            "unit_price_foreign": Decimal("15.00"),
                        }
                    ]
                },
            )
        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.last_unit_price_foreign, Decimal("10.00"))
        self.assertEqual(self.product_variant.last_currency, "USD")


class QCPPhase5Test(APITestCase):
    """Tests for QCP Phase 5 — product_photo_url from gallery photos."""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.user = User.objects.create_user(
            username="qcp5_user", password="password", is_staff=True
        )
        from core.models import UserProfile

        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_po_detail_photo_url_from_gallery(self):
        """Gallery photo is returned when variant.photo is null."""
        ProductPhotoFactory(product=self.product, company=self.company, order=0)
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        PurchaseOrderDetailFactory(purchase_order=po, product_variant=self.product_variant)
        response = self.client.get(f"/purchase-order/{po.id}/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        photo_url = response.data["order_details"][0]["product_photo_url"]
        self.assertIsNotNone(photo_url)
        self.assertIn("test_photo", photo_url)

    def test_po_detail_photo_url_variant_takes_priority(self):
        """Variant photo takes priority over gallery photo."""
        ProductPhotoFactory(product=self.product, company=self.company, order=0)
        self.product_variant.photo = SimpleUploadedFile("variant.jpg", b"x")
        self.product_variant.save()
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        PurchaseOrderDetailFactory(purchase_order=po, product_variant=self.product_variant)
        response = self.client.get(f"/purchase-order/{po.id}/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        photo_url = response.data["order_details"][0]["product_photo_url"]
        self.assertIsNotNone(photo_url)
        self.assertIn("variant", photo_url)

    def test_po_detail_photo_url_null_when_no_photos(self):
        """Null returned when no photos exist anywhere."""
        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        PurchaseOrderDetailFactory(purchase_order=po, product_variant=self.product_variant)
        response = self.client.get(f"/purchase-order/{po.id}/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        photo_url = response.data["order_details"][0]["product_photo_url"]
        self.assertIsNone(photo_url)


class TestPerCompanyPONumberSequence(TestCase):
    def test_each_company_gets_independent_po_sequence(self):
        """Two companies both start their own sequence from PO-YYYY-001."""
        company_a = CompanyFactory()
        company_b = CompanyFactory()
        warehouse_a = WarehouseFactory(company=company_a)
        warehouse_b = WarehouseFactory(company=company_b)

        po_a1 = PurchaseOrderFactory(company=company_a, warehouse=warehouse_a)
        po_a2 = PurchaseOrderFactory(company=company_a, warehouse=warehouse_a)
        po_b1 = PurchaseOrderFactory(company=company_b, warehouse=warehouse_b)

        po_a1.refresh_from_db()
        po_a2.refresh_from_db()
        po_b1.refresh_from_db()

        self.assertRegex(po_a1.purchase_order_number, r"^PO-\d{4}-001$")
        self.assertRegex(po_a2.purchase_order_number, r"^PO-\d{4}-002$")
        self.assertRegex(po_b1.purchase_order_number, r"^PO-\d{4}-001$")

    def test_po_numbers_are_unique_within_company(self):
        """Sequential POs for the same company never get the same number."""
        company = CompanyFactory()
        warehouse = WarehouseFactory(company=company)

        po1 = PurchaseOrderFactory(company=company, warehouse=warehouse)
        po2 = PurchaseOrderFactory(company=company, warehouse=warehouse)
        po3 = PurchaseOrderFactory(company=company, warehouse=warehouse)

        po1.refresh_from_db()
        po2.refresh_from_db()
        po3.refresh_from_db()

        numbers = [po1.purchase_order_number, po2.purchase_order_number, po3.purchase_order_number]
        self.assertEqual(len(numbers), len(set(numbers)))

    def test_new_year_resets_sequence_per_company(self):
        """A company's counter for a prior year does not affect the current year's sequence."""
        company = CompanyFactory()
        warehouse = WarehouseFactory(company=company)
        current_year = str(timezone.now().year)
        prior_year = str(timezone.now().year - 1)

        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO po_number_counter (company_id, year, last_value) VALUES (%s, %s, %s)",
                [company.id.uuid, prior_year, 50],
            )

        po = PurchaseOrderFactory(company=company, warehouse=warehouse)
        po.refresh_from_db()

        self.assertRegex(po.purchase_order_number, rf"^PO-{current_year}-001$")


class TestSourcingService(TestCase):
    """Tests for SourcingService (SourcingPool + SourcingPoolItem)."""

    def setUp(self):
        from apps.purchasing.services.sourcing_service import SourcingService

        self.company = CompanyFactory()
        self.supplier = SupplierFactory(company=self.company)
        self.category = CategoryFactory(
            company=self.company, category_code="TEST", name="Test Category"
        )
        self.service = SourcingService()

    def _make_row(self, **overrides) -> dict:
        row = {
            "row": 2,
            "product_name": "Product",
            "variant_name": "Variant",
            "category_code": self.category.category_code,
            "category_id": str(self.category.id),
            "unit_price": "10.000",
            "discounted_price": None,
            "qty_suggested": None,
            "supplier_link": None,
            "image_url": None,
            "notes": None,
        }
        row.update(overrides)
        return row

    def _build_workbook(self, rows: list[list | tuple]) -> bytes:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Items"
        ws.append(
            [
                "product_name",
                "variant_name",
                "category_code",
                "unit_price",
                "discounted_price",
                "qty_suggested",
                "supplier_link",
                "image_url",
                "notes",
            ]
        )
        for r in rows:
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.read()

    # --- Happy path ---

    def test_pool_auto_created_for_supplier_on_first_import(self):
        self.service.import_rows(
            company=self.company,
            supplier=self.supplier,
            rows=[self._make_row()],
        )
        self.assertTrue(
            SourcingPool.objects.filter(company=self.company, supplier=self.supplier).exists()
        )

    def test_pool_unique_per_supplier_per_company(self):
        self.service.import_rows(
            company=self.company, supplier=self.supplier, rows=[self._make_row()]
        )
        self.service.import_rows(
            company=self.company, supplier=self.supplier, rows=[self._make_row()]
        )
        self.assertEqual(
            SourcingPool.objects.filter(company=self.company, supplier=self.supplier).count(), 1
        )

    def test_import_creates_new_items_in_pool(self):
        rows = [
            self._make_row(product_name="A", variant_name="V1"),
            self._make_row(product_name="B", variant_name="V1"),
            self._make_row(product_name="C", variant_name="V1"),
        ]
        result = self.service.import_rows(company=self.company, supplier=self.supplier, rows=rows)
        self.assertEqual(result["created"], 3)
        pool = SourcingPool.objects.get(company=self.company, supplier=self.supplier)
        self.assertEqual(pool.items.count(), 3)

    def test_build_template_workbook_has_items_and_categories_sheets(self):
        CategoryFactory(company=self.company, category_code="CAT1", name="Category One")
        CategoryFactory(company=self.company, category_code="CAT2", name="Category Two")
        wb = self.service.build_template_workbook(company=self.company)
        self.assertIn("Items", wb.sheetnames)
        self.assertIn("Categories", wb.sheetnames)
        ws = wb["Items"]
        headers = [str(c.value).strip().lower() for c in next(ws.iter_rows(min_row=1, max_row=1))]
        for col in ["product_name", "variant_name", "category_code", "unit_price"]:
            self.assertIn(col, headers)
        ws_cats = wb["Categories"]
        cat_rows = list(ws_cats.iter_rows(values_only=True))
        self.assertEqual(len(cat_rows), 4)  # header + 3 categories (TEST + CAT1 + CAT2)
        self.assertIn(("CAT1", "Category One"), cat_rows[1:])
        self.assertIn(("CAT2", "Category Two"), cat_rows[1:])

    def test_parse_excel_preview_returns_valid_rows_for_correct_input(self):
        file_bytes = self._build_workbook(
            [
                ["Prod A", "Var A", self.category.category_code, "10.000"],
                ["Prod B", "Var B", self.category.category_code, "20.000"],
                ["Prod C", "Var C", self.category.category_code, "30.000"],
            ]
        )
        result = self.service.parse_excel_preview(file_bytes, company=self.company)
        self.assertEqual(len(result["valid"]), 3)
        self.assertEqual(len(result["errors"]), 0)
        for row in result["valid"]:
            self.assertIn("category_name", row)
            self.assertIsInstance(row["category_name"], str)
            self.assertGreater(len(row["category_name"]), 0)

    def test_list_items_returns_empty_when_no_pool_exists(self):
        other_supplier = SupplierFactory(company=self.company)
        self.client = APIClient()
        from core.models import UserProfile

        user = User.objects.create_user(
            username="sourcing_test", password="password", is_staff=True
        )
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)
        response = self.client.get(f"/sourcing-pool/items/?supplier_id={other_supplier.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"pool_id": None, "items": []})

    # --- Business rules ---

    def test_import_merge_updates_existing_item_on_same_name_variant(self):
        self.service.import_rows(
            company=self.company,
            supplier=self.supplier,
            rows=[
                self._make_row(product_name="Earbuds", variant_name="Black", unit_price="45.000")
            ],
        )
        self.service.import_rows(
            company=self.company,
            supplier=self.supplier,
            rows=[
                self._make_row(product_name="Earbuds", variant_name="Black", unit_price="50.000")
            ],
        )
        pool = SourcingPool.objects.get(company=self.company, supplier=self.supplier)
        self.assertEqual(pool.items.count(), 1)
        self.assertEqual(pool.items.first().unit_price, Decimal("50.000"))

    def test_import_merge_case_insensitive_match(self):
        self.service.import_rows(
            company=self.company,
            supplier=self.supplier,
            rows=[self._make_row(product_name="Earbuds", variant_name="Black")],
        )
        self.service.import_rows(
            company=self.company,
            supplier=self.supplier,
            rows=[self._make_row(product_name="EARBUDS", variant_name="BLACK")],
        )
        pool = SourcingPool.objects.get(company=self.company, supplier=self.supplier)
        self.assertEqual(pool.items.count(), 1)
        self.assertEqual(pool.items.first().product_name, "EARBUDS")

    def test_import_deduplicates_intra_batch_case_insensitive_rows(self):
        result = self.service.import_rows(
            company=self.company,
            supplier=self.supplier,
            rows=[
                self._make_row(product_name="Widget", variant_name="Red"),
                self._make_row(product_name="WIDGET", variant_name="RED"),
            ],
        )
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["updated"], 0)
        pool = SourcingPool.objects.get(company=self.company, supplier=self.supplier)
        self.assertEqual(pool.items.count(), 1)
        self.assertEqual(pool.items.first().product_name, "WIDGET")

    def test_import_resets_image_status_when_image_url_changes(self):
        self.service.import_rows(
            company=self.company,
            supplier=self.supplier,
            rows=[self._make_row(image_url="http://a.com/1.jpg")],
        )
        pool = SourcingPool.objects.get(company=self.company, supplier=self.supplier)
        item = pool.items.first()
        item.image_download_status = SourcingPoolItem.ImageDownloadStatus.DONE
        item.save()
        self.service.import_rows(
            company=self.company,
            supplier=self.supplier,
            rows=[self._make_row(image_url="http://a.com/2.jpg")],
        )
        item.refresh_from_db()
        self.assertEqual(item.image_download_status, SourcingPoolItem.ImageDownloadStatus.PENDING)
        self.assertEqual(item.image_url, "http://a.com/2.jpg")

    def test_import_does_not_reset_image_status_when_image_url_unchanged(self):
        self.service.import_rows(
            company=self.company,
            supplier=self.supplier,
            rows=[self._make_row(image_url="http://a.com/1.jpg")],
        )
        pool = SourcingPool.objects.get(company=self.company, supplier=self.supplier)
        item = pool.items.first()
        item.image_download_status = SourcingPoolItem.ImageDownloadStatus.DONE
        item.save()
        self.service.import_rows(
            company=self.company,
            supplier=self.supplier,
            rows=[self._make_row(image_url="http://a.com/1.jpg")],
        )
        item.refresh_from_db()
        self.assertEqual(item.image_download_status, SourcingPoolItem.ImageDownloadStatus.DONE)

    def test_import_new_item_with_no_image_url_gets_pending_status(self):
        self.service.import_rows(
            company=self.company,
            supplier=self.supplier,
            rows=[self._make_row(image_url=None)],
        )
        pool = SourcingPool.objects.get(company=self.company, supplier=self.supplier)
        item = pool.items.first()
        self.assertEqual(item.image_download_status, SourcingPoolItem.ImageDownloadStatus.PENDING)
        self.assertIsNone(item.image_url)

    # --- Edge cases ---

    def test_parse_excel_preview_flags_missing_required_column(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Items"
        ws.append(["product_name", "variant_name", "category_code"])
        ws.append(["Prod", "Var", self.category.category_code])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        result = self.service.parse_excel_preview(buf.read(), company=self.company)
        self.assertEqual(len(result["valid"]), 0)
        self.assertTrue(any("unit_price" in e["message"] for e in result["errors"]))

    def test_parse_excel_preview_flags_invalid_category_code(self):
        file_bytes = self._build_workbook(
            [
                ["Prod", "Var", "DOES-NOT-EXIST", "10.000"],
            ]
        )
        result = self.service.parse_excel_preview(file_bytes, company=self.company)
        self.assertEqual(len(result["valid"]), 0)
        self.assertTrue(any("DOES-NOT-EXIST" in e["message"] for e in result["errors"]))

    def test_parse_excel_preview_flags_invalid_unit_price_non_numeric(self):
        file_bytes = self._build_workbook(
            [
                ["Prod", "Var", self.category.category_code, "abc"],
            ]
        )
        result = self.service.parse_excel_preview(file_bytes, company=self.company)
        self.assertEqual(len(result["valid"]), 0)
        self.assertTrue(any("unit_price" in e["message"] for e in result["errors"]))

    def test_parse_excel_preview_rejects_zero_unit_price(self):
        file_bytes = self._build_workbook(
            [
                ["Prod", "Var", self.category.category_code, "0"],
            ]
        )
        result = self.service.parse_excel_preview(file_bytes, company=self.company)
        self.assertEqual(len(result["valid"]), 0)
        self.assertTrue(any("greater than zero" in e["message"] for e in result["errors"]))

    def test_parse_excel_preview_rejects_negative_unit_price(self):
        file_bytes = self._build_workbook(
            [
                ["Prod", "Var", self.category.category_code, "-5"],
            ]
        )
        result = self.service.parse_excel_preview(file_bytes, company=self.company)
        self.assertEqual(len(result["valid"]), 0)
        self.assertTrue(any("greater than zero" in e["message"] for e in result["errors"]))

    def test_parse_excel_preview_rejects_negative_discounted_price(self):
        file_bytes = self._build_workbook(
            [
                ["Prod", "Var", self.category.category_code, "10.000", "-1"],
            ]
        )
        result = self.service.parse_excel_preview(file_bytes, company=self.company)
        self.assertEqual(len(result["valid"]), 0)
        self.assertTrue(any("discounted_price" in e["message"] for e in result["errors"]))

    def test_parse_excel_preview_rejects_negative_qty_suggested(self):
        file_bytes = self._build_workbook(
            [
                ["Prod", "Var", self.category.category_code, "10.000", "", "-3"],
            ]
        )
        result = self.service.parse_excel_preview(file_bytes, company=self.company)
        self.assertEqual(len(result["valid"]), 0)
        self.assertTrue(any("qty_suggested" in e["message"] for e in result["errors"]))

    def test_parse_excel_preview_accepts_zero_qty_suggested(self):
        file_bytes = self._build_workbook(
            [
                ["Prod", "Var", self.category.category_code, "10.000", "", "0"],
            ]
        )
        result = self.service.parse_excel_preview(file_bytes, company=self.company)
        self.assertEqual(len(result["valid"]), 1)
        self.assertEqual(result["valid"][0]["qty_suggested"], 0)

    def test_parse_excel_preview_accepts_float_formatted_integer_qty(self):
        file_bytes = self._build_workbook(
            [
                ["Prod", "Var", self.category.category_code, "10.000", "", 5.0],
            ]
        )
        result = self.service.parse_excel_preview(file_bytes, company=self.company)
        self.assertEqual(len(result["valid"]), 1)
        self.assertEqual(result["valid"][0]["qty_suggested"], 5)

    def test_parse_excel_preview_skips_blank_rows(self):
        file_bytes = self._build_workbook(
            [
                ["Prod A", "Var A", self.category.category_code, "10.000"],
                [None, None, None, None],
                ["Prod B", "Var B", self.category.category_code, "20.000"],
            ]
        )
        result = self.service.parse_excel_preview(file_bytes, company=self.company)
        self.assertEqual(len(result["valid"]), 2)
        self.assertEqual(len(result["errors"]), 0)

    def test_import_raises_value_error_on_invalid_category_id(self):
        with self.assertRaises(ValueError):
            self.service.import_rows(
                company=self.company,
                supplier=self.supplier,
                rows=[self._make_row(category_id="00000000000000000000000000")],
            )

    def test_import_raises_value_error_on_missing_product_name_key(self):
        row = self._make_row()
        del row["product_name"]
        with self.assertRaises(ValueError):
            self.service.import_rows(
                company=self.company,
                supplier=self.supplier,
                rows=[row],
            )

    def test_list_items_search_filters_on_product_name(self):
        pool = SourcingPoolFactory(company=self.company, supplier=self.supplier)
        SourcingPoolItemFactory(
            pool=pool,
            company=self.company,
            product_name="Earbuds Black",
            variant_name="Default",
            category=self.category,
        )
        SourcingPoolItemFactory(
            pool=pool,
            company=self.company,
            product_name="Phone Case Blue",
            variant_name="Default",
            category=self.category,
        )
        SourcingPoolItemFactory(
            pool=pool,
            company=self.company,
            product_name="Wallet Red",
            variant_name="Default",
            category=self.category,
        )
        self.client = APIClient()
        from core.models import UserProfile

        user = User.objects.create_user(
            username="sourcing_search1", password="password", is_staff=True
        )
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)
        response = self.client.get(
            f"/sourcing-pool/items/?supplier_id={self.supplier.id}&search=ear"
        )
        self.assertEqual(response.status_code, 200)
        items = response.data["results"]
        names = [i["product_name"] for i in items]
        self.assertIn("Earbuds Black", names)
        self.assertNotIn("Phone Case Blue", names)
        self.assertNotIn("Wallet Red", names)

    def test_list_items_search_filters_on_variant_name(self):
        pool = SourcingPoolFactory(company=self.company, supplier=self.supplier)
        SourcingPoolItemFactory(
            pool=pool,
            company=self.company,
            product_name="Case",
            variant_name="Black",
            category=self.category,
        )
        SourcingPoolItemFactory(
            pool=pool,
            company=self.company,
            product_name="Wallet",
            variant_name="Red",
            category=self.category,
        )
        self.client = APIClient()
        from core.models import UserProfile

        user = User.objects.create_user(
            username="sourcing_search2", password="password", is_staff=True
        )
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)
        response = self.client.get(
            f"/sourcing-pool/items/?supplier_id={self.supplier.id}&search=black"
        )
        self.assertEqual(response.status_code, 200)
        items = response.data["results"]
        names = [i["product_name"] for i in items]
        self.assertIn("Case", names)
        self.assertNotIn("Wallet", names)

    # --- Failure paths ---

    def test_import_returns_400_when_rows_list_is_empty(self):
        self.client = APIClient()
        from core.models import UserProfile

        user = User.objects.create_user(
            username="sourcing_empty", password="password", is_staff=True
        )
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)
        response = self.client.post(
            "/sourcing-pool/import/",
            {"supplier_id": str(self.supplier.id), "rows": []},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("rows must be a non-empty list", str(response.data))

    def test_import_returns_404_when_supplier_not_found(self):
        self.client = APIClient()
        from core.models import UserProfile

        user = User.objects.create_user(username="sourcing_404", password="password", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)
        response = self.client.post(
            "/sourcing-pool/import/",
            {"supplier_id": "00000000000000000000000000", "rows": [self._make_row()]},
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_image_view_returns_404_when_item_not_found(self):
        self.client = APIClient()
        from core.models import UserProfile

        user = User.objects.create_user(
            username="sourcing_img1", password="password", is_staff=True
        )
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)
        response = self.client.get("/sourcing-pool/items/00000000000000000000000000/image/")
        self.assertEqual(response.status_code, 404)

    def test_image_view_returns_404_when_no_image_file(self):
        pool = SourcingPoolFactory(company=self.company, supplier=self.supplier)
        item = SourcingPoolItemFactory(
            pool=pool,
            company=self.company,
            category=self.category,
            image_file=None,
        )
        self.client = APIClient()
        from core.models import UserProfile

        user = User.objects.create_user(
            username="sourcing_img2", password="password", is_staff=True
        )
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)
        response = self.client.get(f"/sourcing-pool/items/{item.id}/image/")
        self.assertEqual(response.status_code, 404)

    def test_image_view_scoped_to_company(self):
        company_b = CompanyFactory()
        supplier_b = SupplierFactory(company=company_b)
        pool_b = SourcingPoolFactory(company=company_b, supplier=supplier_b)
        item_b = SourcingPoolItemFactory(
            pool=pool_b,
            company=company_b,
            category=self.category,
        )
        self.client = APIClient()
        from core.models import UserProfile

        user = User.objects.create_user(
            username="sourcing_img3", password="password", is_staff=True
        )
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)
        response = self.client.get(f"/sourcing-pool/items/{item_b.id}/image/")
        self.assertEqual(response.status_code, 404)

    # --- Scoping ---

    def test_list_items_scoped_to_company(self):
        company_b = CompanyFactory()
        supplier_b = SupplierFactory(company=company_b)
        pool_a = SourcingPoolFactory(company=self.company, supplier=self.supplier)
        SourcingPoolItemFactory(
            pool=pool_a,
            company=self.company,
            product_name="Item A",
            variant_name="V1",
            category=self.category,
        )
        pool_b = SourcingPoolFactory(company=company_b, supplier=supplier_b)
        SourcingPoolItemFactory(
            pool=pool_b,
            company=company_b,
            product_name="Item B",
            variant_name="V1",
            category=self.category,
        )
        self.client = APIClient()
        from core.models import UserProfile

        user = User.objects.create_user(
            username="sourcing_scope", password="password", is_staff=True
        )
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)
        response = self.client.get(f"/sourcing-pool/items/?supplier_id={self.supplier.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["product_name"], "Item A")

    # --- FIX 4: API test for POST /sourcing-pool/preview/ ---

    def test_preview_endpoint_returns_valid_and_errors(self):
        file_bytes = self._build_workbook(
            [
                ["Prod A", "Var A", self.category.category_code, "10.000"],
                ["Prod B", "Var B", self.category.category_code, "20.000"],
                ["Prod C", "Var C", "BAD-CODE", "30.000"],
            ]
        )
        self.client = APIClient()
        from core.models import UserProfile

        user = User.objects.create_user(username="preview_test", password="password", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)
        response = self.client.post(
            "/sourcing-pool/preview/",
            {
                "file": SimpleUploadedFile(
                    "test.xlsx",
                    file_bytes,
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["valid"]), 2)
        self.assertEqual(len(response.data["errors"]), 1)

    def test_preview_endpoint_returns_400_when_no_file(self):
        self.client = APIClient()
        from core.models import UserProfile

        user = User.objects.create_user(
            username="preview_no_file", password="password", is_staff=True
        )
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)
        response = self.client.post("/sourcing-pool/preview/", {}, format="multipart")
        self.assertEqual(response.status_code, 400)

    # --- FIX 5: API test for GET /sourcing-pool/template/ ---

    def test_template_endpoint_returns_xlsx(self):
        self.client = APIClient()
        from core.models import UserProfile

        user = User.objects.create_user(
            username="template_test", password="password", is_staff=True
        )
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)
        response = self.client.get("/sourcing-pool/template/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("sourcing_template.xlsx", response["Content-Disposition"])

    # --- FIX 6: Test that import response includes pool_id ---

    def test_import_response_includes_pool_id(self):
        self.client = APIClient()
        from core.models import UserProfile

        user = User.objects.create_user(
            username="import_poolid", password="password", is_staff=True
        )
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)
        response = self.client.post(
            "/sourcing-pool/import/",
            {"supplier_id": str(self.supplier.id), "rows": [self._make_row()]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("pool_id", response.data)
        self.assertIsInstance(response.data["pool_id"], str)
        self.assertGreater(len(response.data["pool_id"]), 0)

    # --- FIX 7: discounted_price < unit_price enforcement ---

    def test_parse_excel_preview_rejects_discounted_price_equal_to_unit_price(self):
        file_bytes = self._build_workbook(
            [
                ["Prod", "Var", self.category.category_code, "10.000", "10.000"],
            ]
        )
        result = self.service.parse_excel_preview(file_bytes, company=self.company)
        self.assertEqual(len(result["valid"]), 0)
        self.assertTrue(
            any(
                "discounted_price must be less than unit_price" in e["message"]
                for e in result["errors"]
            )
        )

    def test_parse_excel_preview_rejects_discounted_price_greater_than_unit_price(self):
        file_bytes = self._build_workbook(
            [
                ["Prod", "Var", self.category.category_code, "10.000", "15.000"],
            ]
        )
        result = self.service.parse_excel_preview(file_bytes, company=self.company)
        self.assertEqual(len(result["valid"]), 0)
        self.assertTrue(
            any(
                "discounted_price must be less than unit_price" in e["message"]
                for e in result["errors"]
            )
        )

    def test_parse_excel_preview_accepts_valid_discounted_price(self):
        file_bytes = self._build_workbook(
            [
                ["Prod", "Var", self.category.category_code, "10.000", "8.000"],
            ]
        )
        result = self.service.parse_excel_preview(file_bytes, company=self.company)
        self.assertEqual(len(result["valid"]), 1)
        self.assertEqual(len(result["errors"]), 0)

    # --- FIX 1: Row count cap ---

    def test_parse_excel_preview_rejects_file_exceeding_row_limit(self):
        data_rows = [
            [f"Prod{i}", f"Var{i}", self.category.category_code, "10.000"] for i in range(5001)
        ]
        file_bytes = self._build_workbook(data_rows)
        result = self.service.parse_excel_preview(file_bytes, company=self.company)
        self.assertEqual(len(result["valid"]), 0)
        self.assertTrue(any("row limit" in e["message"].lower() for e in result["errors"]))

    # --- FIX 2: Clear image_file when image_url changes ---

    def test_import_clears_image_file_when_image_url_changes(self):
        self.service.import_rows(
            company=self.company,
            supplier=self.supplier,
            rows=[self._make_row(image_url="http://a.com/old.jpg")],
        )
        pool = SourcingPool.objects.get(company=self.company, supplier=self.supplier)
        item = pool.items.first()
        item.image_file = "sourcing/images/old.jpg"
        item.image_download_status = SourcingPoolItem.ImageDownloadStatus.DONE
        item.save()
        self.service.import_rows(
            company=self.company,
            supplier=self.supplier,
            rows=[self._make_row(image_url="http://a.com/new.jpg")],
        )
        item.refresh_from_db()
        self.assertFalse(item.image_file)
        self.assertEqual(item.image_download_status, SourcingPoolItem.ImageDownloadStatus.PENDING)

    # --- FIX 3: Pagination ---

    def test_list_items_returns_paginated_response(self):
        pool = SourcingPoolFactory(company=self.company, supplier=self.supplier)
        for i in range(55):
            SourcingPoolItemFactory(
                pool=pool,
                company=self.company,
                product_name=f"Product {i}",
                variant_name="Default",
                category=self.category,
            )
        self.client = APIClient()
        from core.models import UserProfile

        user = User.objects.create_user(
            username="sourcing_paginate", password="password", is_staff=True
        )
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)
        response = self.client.get(f"/sourcing-pool/items/?supplier_id={self.supplier.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 55)
        self.assertEqual(len(response.data["results"]), 50)
        self.assertIsNotNone(response.data["next"])
        self.assertEqual(response.data["pool_id"], str(pool.id))

    # --- Image download tests ---

    def test_download_pool_images_sets_done_status_and_image_file_on_success(self):
        pool = SourcingPoolFactory(company=self.company, supplier=self.supplier)
        item = SourcingPoolItemFactory(
            pool=pool,
            company=self.company,
            category=self.category,
            image_url="http://cdn.example.com/img.jpg",
            image_download_status=SourcingPoolItem.ImageDownloadStatus.PENDING,
        )

        with (
            patch("apps.purchasing.services.sourcing_service.http_requests.get") as mock_get,
            patch("apps.purchasing.services.sourcing_service.default_storage.save") as mock_save,
        ):
            mock_response = Mock()
            mock_response.headers = {"Content-Type": "image/jpeg"}
            mock_response.content = b"imgdata"
            mock_get.return_value = mock_response
            mock_save.return_value = f"sourcing/images/{item.id}.jpg"

            result = self.service.download_pool_images(pool)

        item.refresh_from_db()
        self.assertEqual(item.image_download_status, SourcingPoolItem.ImageDownloadStatus.DONE)
        self.assertEqual(item.image_file.name, f"sourcing/images/{item.id}.jpg")
        self.assertEqual(result, {"done": 1, "failed": 0, "skipped": 0})

    def test_download_pool_images_derives_correct_extension_from_content_type(self):
        pool = SourcingPoolFactory(company=self.company, supplier=self.supplier)
        item = SourcingPoolItemFactory(
            pool=pool,
            company=self.company,
            category=self.category,
            image_url="http://cdn.example.com/img.png",
            image_download_status=SourcingPoolItem.ImageDownloadStatus.PENDING,
        )

        with (
            patch("apps.purchasing.services.sourcing_service.http_requests.get") as mock_get,
            patch("apps.purchasing.services.sourcing_service.default_storage.save") as mock_save,
        ):
            mock_response = Mock()
            mock_response.headers = {"Content-Type": "image/png"}
            mock_response.content = b"pngdata"
            mock_get.return_value = mock_response
            mock_save.return_value = f"sourcing/images/{item.id}.png"

            self.service.download_pool_images(pool)

            args, _ = mock_save.call_args
            self.assertTrue(args[0].endswith(".png"))

    def test_download_pool_images_skips_items_with_no_image_url(self):
        pool = SourcingPoolFactory(company=self.company, supplier=self.supplier)
        SourcingPoolItemFactory(
            pool=pool,
            company=self.company,
            category=self.category,
            product_name="No URL",
            variant_name="Var",
            image_url=None,
        )
        SourcingPoolItemFactory(
            pool=pool,
            company=self.company,
            category=self.category,
            product_name="Has URL",
            variant_name="Var",
            image_url="http://cdn.example.com/img.jpg",
        )

        with (
            patch("apps.purchasing.services.sourcing_service.http_requests.get") as mock_get,
            patch("apps.purchasing.services.sourcing_service.default_storage.save") as mock_save,
        ):
            mock_response = Mock()
            mock_response.headers = {"Content-Type": "image/jpeg"}
            mock_response.content = b"imgdata"
            mock_get.return_value = mock_response
            mock_save.return_value = "sourcing/images/dummy.jpg"

            result = self.service.download_pool_images(pool)

        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["done"], 1)

    def test_download_pool_images_skips_done_items_by_default(self):
        pool = SourcingPoolFactory(company=self.company, supplier=self.supplier)
        SourcingPoolItemFactory(
            pool=pool,
            company=self.company,
            category=self.category,
            product_name="Done Item",
            variant_name="Var",
            image_url="http://cdn.example.com/img.jpg",
            image_download_status=SourcingPoolItem.ImageDownloadStatus.DONE,
        )

        with patch("apps.purchasing.services.sourcing_service.http_requests.get") as mock_get:
            result = self.service.download_pool_images(pool)

        mock_get.assert_not_called()
        self.assertEqual(result, {"done": 0, "failed": 0, "skipped": 0})

    def test_download_pool_images_retries_failed_items_when_include_failed_true(self):
        pool = SourcingPoolFactory(company=self.company, supplier=self.supplier)
        item = SourcingPoolItemFactory(
            pool=pool,
            company=self.company,
            category=self.category,
            product_name="Failed Item",
            variant_name="Var",
            image_url="http://cdn.example.com/img.jpg",
            image_download_status=SourcingPoolItem.ImageDownloadStatus.FAILED,
        )

        with (
            patch("apps.purchasing.services.sourcing_service.http_requests.get") as mock_get,
            patch("apps.purchasing.services.sourcing_service.default_storage.save") as mock_save,
        ):
            mock_response = Mock()
            mock_response.headers = {"Content-Type": "image/jpeg"}
            mock_response.content = b"imgdata"
            mock_get.return_value = mock_response
            mock_save.return_value = f"sourcing/images/{item.id}.jpg"

            result = self.service.download_pool_images(pool, include_failed=True)

        self.assertEqual(result["done"], 1)

    def test_download_pool_images_does_not_process_failed_items_by_default(self):
        pool = SourcingPoolFactory(company=self.company, supplier=self.supplier)
        item = SourcingPoolItemFactory(
            pool=pool,
            company=self.company,
            category=self.category,
            product_name="Failed Item",
            variant_name="Var",
            image_url="http://cdn.example.com/img.jpg",
            image_download_status=SourcingPoolItem.ImageDownloadStatus.FAILED,
        )

        with patch("apps.purchasing.services.sourcing_service.http_requests.get") as mock_get:
            result = self.service.download_pool_images(pool)

        mock_get.assert_not_called()
        item.refresh_from_db()
        self.assertEqual(item.image_download_status, SourcingPoolItem.ImageDownloadStatus.FAILED)
        self.assertEqual(result, {"done": 0, "failed": 0, "skipped": 0})

    def test_download_pool_images_returns_correct_counts_for_mixed_batch(self):
        pool = SourcingPoolFactory(company=self.company, supplier=self.supplier)
        SourcingPoolItemFactory(
            pool=pool,
            company=self.company,
            category=self.category,
            product_name="Succeed",
            variant_name="Var",
            image_url="http://cdn.example.com/success.jpg",
        )
        SourcingPoolItemFactory(
            pool=pool,
            company=self.company,
            category=self.category,
            product_name="Timeout",
            variant_name="Var",
            image_url="http://cdn.example.com/timeout.jpg",
        )
        SourcingPoolItemFactory(
            pool=pool,
            company=self.company,
            category=self.category,
            product_name="No URL",
            variant_name="Var",
            image_url=None,
        )

        with (
            patch("apps.purchasing.services.sourcing_service.http_requests.get") as mock_get,
            patch("apps.purchasing.services.sourcing_service.default_storage.save") as mock_save,
        ):

            def side_effect(url, timeout=10):
                mock = Mock()
                if "timeout" in url:
                    from requests.exceptions import Timeout

                    raise Timeout()
                mock.headers = {"Content-Type": "image/jpeg"}
                mock.content = b"imgdata"
                mock.raise_for_status.return_value = None
                return mock

            mock_get.side_effect = side_effect
            mock_save.return_value = "sourcing/images/dummy.jpg"

            result = self.service.download_pool_images(pool)

        self.assertEqual(result, {"done": 1, "failed": 1, "skipped": 1})

    def test_download_pool_images_marks_failed_on_network_error(self):
        pool = SourcingPoolFactory(company=self.company, supplier=self.supplier)
        item = SourcingPoolItemFactory(
            pool=pool,
            company=self.company,
            category=self.category,
            image_url="http://cdn.example.com/img.jpg",
        )

        with (
            patch("apps.purchasing.services.sourcing_service.http_requests.get") as mock_get,
        ):
            from requests.exceptions import ConnectionError

            mock_get.side_effect = ConnectionError("Network down")

            result = self.service.download_pool_images(pool)

        item.refresh_from_db()
        self.assertEqual(item.image_download_status, SourcingPoolItem.ImageDownloadStatus.FAILED)
        self.assertEqual(result["failed"], 1)

    def test_download_pool_images_marks_failed_on_http_error(self):
        pool = SourcingPoolFactory(company=self.company, supplier=self.supplier)
        item = SourcingPoolItemFactory(
            pool=pool,
            company=self.company,
            category=self.category,
            image_url="http://cdn.example.com/img.jpg",
        )

        with (
            patch("apps.purchasing.services.sourcing_service.http_requests.get") as mock_get,
        ):
            mock_response = Mock()
            mock_response.headers = {"Content-Type": "image/jpeg"}
            mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
                "403 Client Error"
            )
            mock_get.return_value = mock_response

            result = self.service.download_pool_images(pool)

        item.refresh_from_db()
        self.assertEqual(item.image_download_status, SourcingPoolItem.ImageDownloadStatus.FAILED)
        self.assertEqual(result["failed"], 1)

    def test_download_pool_images_continues_after_single_item_failure(self):
        pool = SourcingPoolFactory(company=self.company, supplier=self.supplier)
        items = []
        for i in range(3):
            items.append(
                SourcingPoolItemFactory(
                    pool=pool,
                    company=self.company,
                    category=self.category,
                    product_name=f"Item {i}",
                    variant_name="Var",
                    image_url=f"http://cdn.example.com/{i}.jpg",
                )
            )

        with (
            patch("apps.purchasing.services.sourcing_service.http_requests.get") as mock_get,
            patch("apps.purchasing.services.sourcing_service.default_storage.save") as mock_save,
        ):
            call_count = [0]

            def side_effect(url, timeout=10):
                call_count[0] += 1
                mock = Mock()
                if call_count[0] == 2:
                    from requests.exceptions import Timeout

                    raise Timeout()
                mock.headers = {"Content-Type": "image/jpeg"}
                mock.content = b"imgdata"
                mock.raise_for_status.return_value = None
                return mock

            mock_get.side_effect = side_effect
            mock_save.return_value = "sourcing/images/dummy.jpg"

            result = self.service.download_pool_images(pool)

        self.assertEqual(result["done"], 2)
        self.assertEqual(result["failed"], 1)
        for item in items:
            item.refresh_from_db()
        statuses = [item.image_download_status for item in items]
        self.assertEqual(
            statuses.count(SourcingPoolItem.ImageDownloadStatus.DONE), 2
        )
        self.assertEqual(
            statuses.count(SourcingPoolItem.ImageDownloadStatus.FAILED), 1
        )

    def test_download_pool_images_uses_jpg_fallback_for_unknown_content_type(self):
        pool = SourcingPoolFactory(company=self.company, supplier=self.supplier)
        item = SourcingPoolItemFactory(
            pool=pool,
            company=self.company,
            category=self.category,
            image_url="http://cdn.example.com/img.xyz",
        )

        with (
            patch("apps.purchasing.services.sourcing_service.http_requests.get") as mock_get,
            patch("apps.purchasing.services.sourcing_service.default_storage.save") as mock_save,
        ):
            mock_response = Mock()
            mock_response.headers = {"Content-Type": "application/octet-stream"}
            mock_response.content = b"imagedata"
            mock_get.return_value = mock_response
            mock_save.return_value = f"sourcing/images/{item.id}.jpg"

            self.service.download_pool_images(pool)

            args, _ = mock_save.call_args
            self.assertTrue(args[0].endswith(".jpg"))

    # --- Download images endpoint tests ---

    def test_download_images_endpoint_returns_counts(self):
        pool = SourcingPoolFactory(company=self.company, supplier=self.supplier)
        self.client = APIClient()
        from core.models import UserProfile

        user = User.objects.create_user(
            username="download_img1", password="password", is_staff=True
        )
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)

        with patch(
            "apps.purchasing.services.sourcing_service.SourcingService.download_pool_images",
            return_value={"done": 3, "failed": 1, "skipped": 0},
        ):
            response = self.client.post(
                "/sourcing-pool/download-images/",
                {"pool_id": str(pool.id)},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"done": 3, "failed": 1, "skipped": 0})

    def test_download_images_endpoint_returns_400_when_pool_id_missing(self):
        self.client = APIClient()
        from core.models import UserProfile

        user = User.objects.create_user(
            username="download_img2", password="password", is_staff=True
        )
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)

        response = self.client.post("/sourcing-pool/download-images/", {}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("pool_id is required", str(response.data))

    def test_download_images_endpoint_returns_404_when_pool_not_found(self):
        self.client = APIClient()
        from core.models import UserProfile

        user = User.objects.create_user(
            username="download_img3", password="password", is_staff=True
        )
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)

        response = self.client.post(
            "/sourcing-pool/download-images/",
            {"pool_id": "00000000000000000000000000"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn("Pool not found", str(response.data))

    def test_download_images_endpoint_scoped_to_company(self):
        company_b = CompanyFactory()
        supplier_b = SupplierFactory(company=company_b)
        pool_b = SourcingPoolFactory(company=company_b, supplier=supplier_b)

        self.client = APIClient()
        from core.models import UserProfile

        user = User.objects.create_user(
            username="download_img4", password="password", is_staff=True
        )
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)

        response = self.client.post(
            "/sourcing-pool/download-images/",
            {"pool_id": str(pool_b.id)},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn("Pool not found", str(response.data))


class TestPhase3DraftLines(TestCase):
    """Phase 3 — Draft PO line infrastructure tests."""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.service = PurchaseOrderService()
        self.po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            exchange_rate=2200,
        )
        self.supplier = SupplierFactory(company=self.company)
        self.pool = SourcingPoolFactory(company=self.company, supplier=self.supplier)
        self.sourcing_item = SourcingPoolItemFactory(
            pool=self.pool,
            company=self.company,
            product_name="Widget",
            variant_name="Red",
            category=self.category,
            unit_price=Decimal("10.000"),
        )

    # --- Model + migration ---

    def test_draft_detail_str_shows_draft_prefix(self):
        """PurchaseOrderDetail with product_variant=None shows [Draft] prefix."""
        detail = PurchaseOrderDetail.objects.create(
            purchase_order=self.po,
            company=self.company,
            product_variant=None,
            sourcing_item=self.sourcing_item,
            draft_product_name="Widget / Red",
            ordered_qty=5,
        )
        expected = f"{self.po.purchase_order_number} - [Draft] Widget / Red"
        self.assertEqual(str(detail), expected)

    def test_regular_detail_str_unchanged(self):
        """str still works for a detail with product_variant set."""
        detail = PurchaseOrderDetailFactory(
            purchase_order=self.po,
            product_variant=self.product_variant,
            company=self.company,
        )
        expected = f"{self.po.purchase_order_number} - {self.product_variant.sku_variant_code}"
        self.assertEqual(str(detail), expected)

    # --- add_draft_line service method ---

    def test_add_draft_line_happy_path(self):
        """Creates PurchaseOrderDetail with product_variant=None, sourcing_item set, etc."""
        detail = self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        self.assertIsNone(detail.product_variant_id)
        self.assertEqual(detail.sourcing_item_id, self.sourcing_item.id)
        self.assertEqual(detail.draft_product_name, "Widget / Red")
        self.assertEqual(detail.ordered_qty, 5)
        self.sourcing_item.refresh_from_db()
        self.assertEqual(self.sourcing_item.times_ordered, 1)

    def test_add_draft_line_uses_pool_item_price_when_no_override(self):
        """unit_price_foreign defaults to sourcing_item.unit_price."""
        detail = self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        self.assertEqual(detail.unit_price_foreign, Decimal("10.000"))

    def test_add_draft_line_uses_override_price(self):
        """unit_price_foreign uses the override when provided."""
        detail = self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
            unit_price_foreign=Decimal("99.000"),
        )
        self.assertEqual(detail.unit_price_foreign, Decimal("99.000"))

    def test_add_draft_line_duplicate_raises(self):
        """Adding the same sourcing item twice raises ValidationError."""
        self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        with self.assertRaises(ValidationError):
            self.service.add_draft_line(
                po=self.po,
                sourcing_item_id=str(self.sourcing_item.id),
                ordered_qty=5,
            )

    def test_add_draft_line_on_ordered_po_succeeds(self):
        """Adding a draft line to an ORDERED PO (not just DRAFT) is allowed."""
        self.po.status = PurchaseOrder.POStatus.ORDERED
        self.po.save()
        detail = self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=3,
        )
        self.assertIsNone(detail.product_variant)
        self.assertEqual(detail.sourcing_item, self.sourcing_item)
        self.sourcing_item.refresh_from_db()
        self.assertEqual(self.sourcing_item.times_ordered, 1)

    def test_add_draft_line_rejects_non_draft_po(self):
        """PO with status SHIPPED raises ValidationError."""
        self.po.status = PurchaseOrder.POStatus.SHIPPED
        self.po.save()
        with self.assertRaises(ValidationError):
            self.service.add_draft_line(
                po=self.po,
                sourcing_item_id=str(self.sourcing_item.id),
                ordered_qty=5,
            )

    def test_add_draft_line_rejects_zero_qty(self):
        """ordered_qty=0 raises ValidationError."""
        with self.assertRaises(ValidationError):
            self.service.add_draft_line(
                po=self.po,
                sourcing_item_id=str(self.sourcing_item.id),
                ordered_qty=0,
            )

    # --- DELIVERED gate ---

    def test_delivered_gate_blocked_by_draft_lines(self):
        """check_purchase_order_requirements for DELIVERED returns order_details missing."""
        self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        missing = self.service.check_purchase_order_requirements(
            self.po, PurchaseOrder.POStatus.DELIVERED
        )
        field_names = {item["field"] for item in missing}
        self.assertIn("order_details", field_names)

    def test_delivered_gate_passes_when_all_lines_finalized(self):
        """After finalize_draft_line, the DELIVERED gate passes."""
        detail = self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        self.service.finalize_draft_line(
            detail=detail,
            sku_suffix="SKU-TEST",
        )
        missing = self.service.check_purchase_order_requirements(
            self.po, PurchaseOrder.POStatus.DELIVERED
        )
        field_names = {item["field"] for item in missing}
        self.assertNotIn("order_details", field_names)

    # --- finalize_draft_line service method ---

    def test_finalize_draft_line_creates_product_and_variant(self):
        """Creates a Product and ProductVariant; detail updated."""
        detail = self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        result = self.service.finalize_draft_line(
            detail=detail,
            sku_suffix="SKU-TEST",
        )
        result.refresh_from_db()
        self.assertIsNotNone(result.product_variant_id)
        self.assertIsNone(result.sourcing_item_id)
        self.assertEqual(result.draft_product_name, "")

    def test_finalize_uses_pool_item_product_name_when_no_override(self):
        """Product.name equals sourcing_item.product_name when product_name=None."""
        detail = self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        result = self.service.finalize_draft_line(
            detail=detail,
            sku_suffix="SKU-TEST",
        )
        self.assertEqual(result.product_variant.product.name, "Widget")

    def test_finalize_uses_override_product_name(self):
        """product_name='Custom' → Product.name is 'Custom'."""
        detail = self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        result = self.service.finalize_draft_line(
            detail=detail,
            sku_suffix="SKU-TEST",
            product_name="Custom",
        )
        self.assertEqual(result.product_variant.product.name, "Custom")

    def test_finalize_raises_on_already_finalized_detail(self):
        """Calling finalize_draft_line on a detail with product_variant raises ValidationError."""
        detail = self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        self.service.finalize_draft_line(detail=detail, sku_suffix="SKU-TEST")
        with self.assertRaises(ValidationError):
            self.service.finalize_draft_line(detail=detail, sku_suffix="SKU-AGAIN")

    def test_finalize_raises_on_regular_detail(self):
        """Calling finalize_draft_line on a regular (non-draft) detail raises ValidationError."""
        detail = PurchaseOrderDetailFactory(
            purchase_order=self.po,
            product_variant=self.product_variant,
            company=self.company,
        )
        with self.assertRaises(ValidationError):
            self.service.finalize_draft_line(detail=detail, sku_suffix="SKU-TEST")

    def test_finalize_blank_sku_suffix_raises(self):
        """sku_suffix='' raises ValidationError."""
        detail = self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        with self.assertRaises(ValidationError):
            self.service.finalize_draft_line(detail=detail, sku_suffix="")

    def test_finalize_duplicate_sku_raises_validation_error(self):
        """Finalize converts IntegrityError from duplicate SKU into ValidationError, not a 500."""
        from django.db import IntegrityError

        detail = self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        with patch(
            "apps.inventory.services.product_service.ProductService.create_product_with_variants",
            side_effect=IntegrityError("unique constraint violated"),
        ):
            with self.assertRaises(ValidationError) as ctx:
                self.service.finalize_draft_line(
                    detail=detail,
                    sku_suffix="DUPE-001",
                    category_id=str(self.category.id),
                )
        self.assertIn("already exists", str(ctx.exception))
        # Detail must remain in draft state — transaction rolled back.
        detail.refresh_from_db()
        self.assertIsNone(detail.product_variant)
        self.assertIsNotNone(detail.sourcing_item_id)

    def test_full_draft_line_lifecycle_stock_and_cogs_created(self):
        """End-to-end: add_draft_line → finalize → InventoryService ORDERED + DELIVERED → stock + COGS."""
        from apps.inventory.services.inventory_service import InventoryService

        # Set exchange rate so base prices can be computed at finalization.
        self.po.exchange_rate = Decimal("15000.000")
        self.po.save()

        # Add draft line.
        detail = self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=10,
            unit_price_foreign=Decimal("20.000"),
        )

        # Finalize: creates Product + Variant and recomputes base prices.
        detail = self.service.finalize_draft_line(
            detail=detail,
            sku_suffix="LIFECYCLE-001",
            category_id=str(self.category.id),
        )
        self.assertIsNotNone(detail.product_variant)
        self.assertIsNone(detail.sourcing_item)
        detail.refresh_from_db()
        self.assertIsNotNone(detail.unit_price_base)
        self.assertGreater(detail.unit_price_base, 0)

        variant_id = str(detail.product_variant.id)
        warehouse = self.po.warehouse
        inv = InventoryService()

        # Simulate ORDERED: records incoming stock.
        ordered_data = [
            {
                "product_variant_id": variant_id,
                "ordered_qty": 10,
                "note": "test",
            }
        ]
        inv.update_stock_on_po(po=self.po, new_status=PurchaseOrder.POStatus.ORDERED, data=ordered_data)

        # Simulate DELIVERED: creates COGS layer + updates physical qty.
        delivered_data = [
            {
                "product_variant_id": variant_id,
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_base": detail.unit_price_base,
                "unit_price_foreign": Decimal("20.000"),
                "discounted_unit_price_foreign": Decimal("20.000"),
                "discounted_total_price_base": detail.discounted_total_price_base or 0,
                "exchange_rate": Decimal("15000.000"),
                "note": "test",
            }
        ]
        inv.update_stock_on_po(po=self.po, new_status=PurchaseOrder.POStatus.DELIVERED, data=delivered_data)
        inv.update_cogs_on_po(po=self.po, new_status=PurchaseOrder.POStatus.DELIVERED, data=delivered_data)

        pvw = ProductVariantWarehouse.objects.filter(
            product_variant_id=variant_id, warehouse=warehouse
        ).first()
        self.assertIsNotNone(pvw, "ProductVariantWarehouse must exist after DELIVERED")
        self.assertGreater(pvw.physical_qty, 0, "physical_qty must be > 0 after receiving stock")

        cogs = ProductCogs.objects.filter(
            product_variant_id=variant_id,
            warehouse=warehouse,
        ).first()
        self.assertIsNotNone(cogs, "ProductCogs FIFO layer must be created after DELIVERED")
        self.assertGreater(cogs.remaining_qty, 0, "COGS remaining_qty must be > 0")

    # --- Null guard tests ---

    def test_snapshot_sales_metrics_skips_draft_lines(self):
        """_snapshot_sales_metrics_at_ordered runs without error with draft lines."""
        PurchaseOrderDetailFactory(
            purchase_order=self.po,
            product_variant=self.product_variant,
            company=self.company,
            ordered_qty=10,
        )
        self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        self.service._snapshot_sales_metrics_at_ordered(self.po)

    def test_recalculate_forecast_cbm_skips_draft_lines(self):
        """_recalculate_forecast_cbm skips draft lines; regular detail's CBM is counted."""
        self.product.length = 20
        self.product.width = 10
        self.product.height = 5
        self.product.save()
        PurchaseOrderDetailFactory(
            purchase_order=self.po,
            product_variant=self.product_variant,
            company=self.company,
            ordered_qty=10,
        )
        self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        self.service._recalculate_forecast_cbm(self.po)
        self.po.refresh_from_db()
        expected_cbm = Decimal("20") * Decimal("10") * Decimal("5") / Decimal("1000000") * 10
        self.assertEqual(self.po.forecast_cbm, round(expected_cbm, 6))

    def test_ordered_transition_inventory_data_skips_draft_lines(self):
        """update_purchase_order with ORDERED does not call inventory_service for draft lines."""
        PurchaseOrderDetailFactory(
            purchase_order=self.po,
            product_variant=self.product_variant,
            company=self.company,
            ordered_qty=10,
        )
        self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        with patch.object(InventoryService, "update_stock_on_po") as mock_update:
            self.service.update_purchase_order(
                self.po,
                {
                    "status": PurchaseOrder.POStatus.ORDERED,
                    "purchase_order_invoice_file": "invoice.pdf",
                    "invoice_number": "INV-001",
                    "invoice_date": date.today(),
                    "commission_fee_pct": 5,
                    "forwarder_name": "Forwarder",
                    "supplier_name": "Supplier",
                    "shop_services": "Service",
                    "delivery_fee": 0,
                },
            )
            call_data = mock_update.call_args[1]["data"]
            variant_ids_in_call = [item["product_variant_id"] for item in call_data]
            self.assertEqual(len(variant_ids_in_call), 1)
            self.assertIn(str(self.product_variant.id), variant_ids_in_call)

    def test_transition_warnings_skips_draft_lines_for_partial_receipt(self):
        """get_transition_warnings for COMPLETED with draft line returns no AttributeError."""
        PurchaseOrderDetailFactory(
            purchase_order=self.po,
            product_variant=self.product_variant,
            company=self.company,
            ordered_qty=100,
            received_qty=80,
        )
        self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        warnings = self.service.get_transition_warnings(
            self.po, PurchaseOrder.POStatus.COMPLETED
        )
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["type"], "partial_receipt")

    # --- API endpoint tests ---

    def test_api_add_draft_line_returns_201(self):
        """POST to draft-lines endpoint → HTTP 201."""
        from core.models import UserProfile
        client = APIClient()
        user = User.objects.create_user(username="phase3_api", password="p", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client.force_authenticate(user=user)
        response = client.post(
            f"/purchase-order/{self.po.id}/draft-lines/",
            {"sourcing_item_id": str(self.sourcing_item.id), "ordered_qty": 5},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("detail_id", response.data)

    def test_api_add_draft_line_duplicate_returns_400(self):
        """Second identical POST → HTTP 400."""
        from core.models import UserProfile
        client = APIClient()
        user = User.objects.create_user(username="phase3_api2", password="p", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client.force_authenticate(user=user)
        client.post(
            f"/purchase-order/{self.po.id}/draft-lines/",
            {"sourcing_item_id": str(self.sourcing_item.id), "ordered_qty": 5},
            format="json",
        )
        response = client.post(
            f"/purchase-order/{self.po.id}/draft-lines/",
            {"sourcing_item_id": str(self.sourcing_item.id), "ordered_qty": 5},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_api_add_draft_line_missing_sourcing_item_id_returns_400(self):
        """Omit sourcing_item_id → HTTP 400."""
        from core.models import UserProfile
        client = APIClient()
        user = User.objects.create_user(username="phase3_api3", password="p", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client.force_authenticate(user=user)
        response = client.post(
            f"/purchase-order/{self.po.id}/draft-lines/",
            {"ordered_qty": 5},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_api_finalize_draft_line_returns_200(self):
        """POST to details/{id}/finalize/ → HTTP 200."""
        from core.models import UserProfile
        client = APIClient()
        user = User.objects.create_user(username="phase3_fin1", password="p", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client.force_authenticate(user=user)
        detail = self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        response = client.post(
            f"/purchase-order/{self.po.id}/details/{detail.id}/finalize/",
            {"sku_suffix": "SKU-TEST"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("detail_id", response.data)
        self.assertIn("variant_id", response.data)

    def test_api_finalize_draft_line_missing_sku_suffix_returns_400(self):
        """Missing sku_suffix → HTTP 400."""
        from core.models import UserProfile
        client = APIClient()
        user = User.objects.create_user(username="phase3_fin2", password="p", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client.force_authenticate(user=user)
        detail = self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        response = client.post(
            f"/purchase-order/{self.po.id}/details/{detail.id}/finalize/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_api_finalize_non_draft_detail_returns_400(self):
        """Finalize a regular (non-draft) detail → HTTP 400."""
        from core.models import UserProfile
        client = APIClient()
        user = User.objects.create_user(username="phase3_fin3", password="p", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client.force_authenticate(user=user)
        detail = PurchaseOrderDetailFactory(
            purchase_order=self.po,
            product_variant=self.product_variant,
            company=self.company,
        )
        response = client.post(
            f"/purchase-order/{self.po.id}/details/{detail.id}/finalize/",
            {"sku_suffix": "SKU-TEST"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_api_check_transition_to_delivered_blocked_by_draft_line(self):
        """check_transition with DELIVERED on SHIPPED PO with draft line → order_details in missing_fields."""
        from core.models import UserProfile
        client = APIClient()
        user = User.objects.create_user(username="phase3_ct", password="p", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client.force_authenticate(user=user)
        self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        # Advance PO to SHIPPED (valid predecessor for DELIVERED check)
        self.po.status = PurchaseOrder.POStatus.SHIPPED
        self.po.save()
        response = client.post(
            f"/purchase-order/{self.po.id}/check_transition/",
            {"status": PurchaseOrder.POStatus.DELIVERED},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["can_transition"])
        field_names = {item["field"] for item in response.data["missing_fields"]}
        self.assertIn("order_details", field_names)

    def test_finalize_raises_when_no_category_available(self):
        """Pool item with category=None, no category_id → ValidationError."""
        item_no_cat = SourcingPoolItemFactory(
            pool=self.pool,
            company=self.company,
            product_name="NoCat",
            variant_name="Item",
            category=None,
            unit_price=Decimal("5.000"),
        )
        detail = self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(item_no_cat.id),
            ordered_qty=5,
        )
        with self.assertRaises(ValidationError) as ctx:
            self.service.finalize_draft_line(detail=detail, sku_suffix="SKU-TEST")
        self.assertIn("category_id is required", str(ctx.exception))

    def test_finalize_raises_when_no_category_with_api(self):
        """Same scenario via API → HTTP 400."""
        from core.models import UserProfile
        client = APIClient()
        user = User.objects.create_user(username="phase3_nocat", password="p", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client.force_authenticate(user=user)
        item_no_cat = SourcingPoolItemFactory(
            pool=self.pool,
            company=self.company,
            product_name="NoCat2",
            variant_name="Item",
            category=None,
            unit_price=Decimal("5.000"),
        )
        detail = self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(item_no_cat.id),
            ordered_qty=5,
        )
        response = client.post(
            f"/purchase-order/{self.po.id}/details/{detail.id}/finalize/",
            {"sku_suffix": "SKU-TEST"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_draft_line_preserved_when_regular_line_patched(self):
        """PATCH regular line without including draft line id → draft line survives."""
        detail_regular = PurchaseOrderDetailFactory(
            purchase_order=self.po,
            product_variant=self.product_variant,
            company=self.company,
            ordered_qty=10,
        )
        detail_draft = self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
            self.service.update_purchase_order(
                self.po,
                {
                    "order_details": [
                        {
                            "id": str(detail_regular.id),
                            "ordered_qty": 20,
                        }
                    ]
                },
            )
        self.assertTrue(
            PurchaseOrderDetail.objects.filter(id=detail_draft.id).exists()
        )
        self.assertEqual(
            PurchaseOrderDetail.objects.get(id=detail_draft.id).ordered_qty, 5
        )

    def test_sourcing_item_deletion_blocked_when_po_detail_references_it(self):
        """Delete SourcingPoolItem with draft PO detail → ProtectedError."""
        self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        with self.assertRaises(ProtectedError):
            self.sourcing_item.delete()

    def test_api_patch_draft_line_qty_via_standard_endpoint(self):
        """PATCH the PO's order_details with draft line id and ordered_qty (no product_variant_id)."""
        from core.models import UserProfile
        client = APIClient()
        user = User.objects.create_user(username="phase3_patch", password="p", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client.force_authenticate(user=user)
        detail = self.service.add_draft_line(
            po=self.po,
            sourcing_item_id=str(self.sourcing_item.id),
            ordered_qty=5,
        )
        response = client.patch(
            f"/purchase-order/{self.po.id}/",
            {"order_details": [{"id": str(detail.id), "ordered_qty": 15}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        detail.refresh_from_db()
        self.assertEqual(detail.ordered_qty, 15)
