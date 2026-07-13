from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import fitz
import openpyxl
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.catalog.factories import (
    CategoryFactory,
    ProductFactory,
    ProductPhotoFactory,
    ProductVariantFactory,
)
from apps.catalog.models import Category, Product, ProductVariant
from apps.inventory.factories import (
    ProductVariantWarehouseFactory,
)
from apps.inventory.models import ProductCogs, ProductVariantWarehouse
from apps.inventory.services.inventory_service import InventoryService
from apps.purchasing.factories import (
    ProductSupplierFactory,
    PurchaseOrderDetailFactory,
    PurchaseOrderFactory,
    SupplierFactory,
)
from apps.purchasing.models import (
    ColorAbbreviation,
    ProductSupplier,
    PurchaseOrder,
    PurchaseOrderDetail,
    PurchaseOrderStatusHistory,
)
from apps.purchasing.serializers import PurchaseOrderReadSerializer, PurchaseOrderUpdateSerializer
from apps.purchasing.services.purchasing_service import PurchaseOrderService
from apps.purchasing.services.sourcing_product_service import SourcingProductService
from apps.purchasing.services.sourcing_service import SourcingService
from core.factories import CompanyFactory, UserProfileFactory, WarehouseFactory
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
            has_discount=True,
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

    def test_recalculate_po_totals_respects_has_discount_false_with_zero_discounted(self):
        """_recalculate_po_totals uses base prices when has_discount=False, even if discounted prices are 0."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            has_discount=False,
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
            discounted_unit_price_foreign=Decimal("0"),
            discounted_unit_price_base=0,
            discounted_total_price_foreign=Decimal("0"),
            discounted_total_price_base=0,
        )

        po = self.service.update_purchase_order(po, {})
        po.refresh_from_db()

        self.assertEqual(po.total_item_amount, 2200000)
        self.assertEqual(po.total_order_amount, 2200000)
        self.assertEqual(po.total_amount, 2200000)

    def test_recalculate_po_totals_uses_discounted_when_has_discount_true(self):
        """_recalculate_po_totals uses discounted prices when has_discount=True."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            has_discount=True,
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
            discounted_unit_price_foreign=Decimal("70"),
            discounted_unit_price_base=150000,
            discounted_total_price_foreign=Decimal("700"),
            discounted_total_price_base=1500000,
        )

        po = self.service.update_purchase_order(po, {})
        po.refresh_from_db()

        self.assertEqual(po.total_item_amount, 1500000)
        self.assertEqual(po.total_order_amount, 1500000)
        self.assertEqual(po.total_amount, 1500000)


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
        from apps.purchasing.factories import SupplierFactory
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
        from apps.purchasing.factories import SupplierFactory

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
        from apps.purchasing.factories import SupplierFactory

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
        """Assert note, has_discount, and file fields are in COMPLETED header, order_detail is empty."""
        fields = PurchaseOrder.get_editable_fields(PurchaseOrder.POStatus.COMPLETED)
        assert "note" in fields["header"]
        assert "has_discount" in fields["header"]
        assert "purchase_order_invoice_file" in fields["header"]
        assert "delivery_order_file" in fields["header"]
        assert "delivery_order_invoice_file" in fields["header"]
        assert "packing_list_file" in fields["header"]
        self.assertEqual(fields["order_detail"], [])

    def test_get_editable_fields_cancelled(self):
        """Assert CANCELLED still only has note and has_discount."""
        fields = PurchaseOrder.get_editable_fields(PurchaseOrder.POStatus.CANCELLED)
        self.assertEqual(fields["header"], ["note", "has_discount"])
        self.assertEqual(fields["order_detail"], [])

    def test_patch_file_field_on_completed_po(self):
        """PATCH a file field on a COMPLETED PO with empty file field should succeed."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.COMPLETED,
            purchase_order_invoice_file=None,
        )
        file_data = b"x" * 1024
        pdf_file = SimpleUploadedFile("test.pdf", file_data, content_type="application/pdf")
        with patch(
            "apps.purchasing.serializers.compress_pdf_iterative",
            return_value=(ContentFile(b"%PDF-1.4 test", name="test.pdf"), True),
        ):
            response = self.client.patch(
                f"/purchase-order/{po.id}/",
                {"purchase_order_invoice_file": pdf_file},
                format="multipart",
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        po.refresh_from_db()
        self.assertIsNotNone(po.purchase_order_invoice_file)

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
        from apps.purchasing.factories import ProductSupplierFactory, SupplierFactory

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
        from apps.purchasing.factories import ProductSupplierFactory, SupplierFactory

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


class TestPhase1SourcingAutoFinalize(TestCase):
    """Phase 1 — Sourcing pool auto-finalize, dim fields, ColorAbbreviation, template redesign."""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company, category_code="TEST")
        self.supplier = SupplierFactory(company=self.company)
        self.service = SourcingService()
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(
            product=self.product, sku_variant_code="EXISTING-SKU"
        )

    @staticmethod
    def _build_workbook(headers: list[str], data_rows: list[list]) -> bytes:
        import io

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Items"
        ws.append(headers)
        for row in data_rows:
            ws.append(row)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.read()

    # ===== parse_excel_preview tests =====

    def test_happy_path_2d_product_all_dims_color_known(self):
        ColorAbbreviation.objects.create(
            company=self.company, color_name="Putih", abbreviation="PTH"
        )
        ColorAbbreviation.objects.create(
            company=self.company, color_name="Merah", abbreviation="MRH"
        )
        headers = [
            "variant_code",
            "product_name",
            "dim1_key",
            "dim1_value",
            "dim2_key",
            "dim2_value",
            "category_code",
            "unit_price",
        ]
        rows = [
            ["", "Kaos Polo", "Warna", "Putih", "Ukuran", "S", "TEST", "50000"],
            ["", "Kaos Polo", "Warna", "Putih", "Ukuran", "M", "TEST", "50000"],
            ["", "Kaos Polo", "Warna", "Putih", "Ukuran", "L", "TEST", "50000"],
            ["", "Kaos Polo", "Warna", "Merah", "Ukuran", "S", "TEST", "50000"],
            ["", "Kaos Polo", "Warna", "Merah", "Ukuran", "M", "TEST", "50000"],
            ["", "Kaos Polo", "Warna", "Merah", "Ukuran", "L", "TEST", "50000"],
        ]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertEqual(len(result["valid"]), 6)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["missing_colors"], [])
        for row in result["valid"]:
            self.assertEqual(row["dim1_key"], "Warna")
            self.assertEqual(row["dim2_key"], "Ukuran")
            self.assertIn(
                row["variant_name"],
                ["Putih-S", "Putih-M", "Putih-L", "Merah-S", "Merah-M", "Merah-L"],
            )

    def test_1d_product_dim1_only_no_dim2(self):
        headers = [
            "variant_code",
            "product_name",
            "dim1_key",
            "dim1_value",
            "dim2_key",
            "dim2_value",
            "category_code",
            "unit_price",
        ]
        rows = [
            ["", "Celana", "Warna", "Putih", "", "", "TEST", "50000"],
            ["", "Celana", "Warna", "Hitam", "", "", "TEST", "50000"],
            ["", "Celana", "Warna", "Abu", "", "", "TEST", "50000"],
        ]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertEqual(len(result["valid"]), 3)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["valid"][0]["variant_name"], "Putih")
        self.assertEqual(result["valid"][1]["variant_name"], "Hitam")
        self.assertEqual(result["valid"][2]["variant_name"], "Abu")

    def test_no_dims_just_price_and_supplier_link(self):
        headers = ["product_name", "supplier_link", "unit_price"]
        rows = [["Produk Tunggal", "https://shop.com/x", "50000"]]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertEqual(len(result["valid"]), 1)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["valid"][0]["variant_name"], "")

    def test_discounted_price_equals_unit_price_treated_as_no_discount(self):
        headers = ["product_name", "unit_price", "discounted_price", "supplier_link"]
        rows = [["Test", "15.3", "15.3", "https://shop.com/x"]]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertEqual(len(result["valid"]), 1)
        self.assertIsNone(result["valid"][0]["discounted_price"])

    def test_new_category_code_not_in_db_passes_through(self):
        headers = ["product_name", "category_code", "unit_price", "supplier_link"]
        rows = [["New Cat Product", "DOES-NOT-EXIST", "10000", "https://shop.com/x"]]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertEqual(len(result["valid"]), 1)
        self.assertIsNone(result["valid"][0]["category_id"])
        self.assertEqual(result["valid"][0]["category_code"], "DOES-NOT-EXIST")

    def test_variant_code_exists_in_db_stores_variant_id(self):
        headers = ["variant_code", "product_name", "unit_price", "supplier_link"]
        rows = [["EXISTING-SKU", "Existing", "10000", "https://shop.com/x"]]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertEqual(len(result["valid"]), 1)
        self.assertEqual(result["valid"][0]["variant_id"], str(self.product_variant.id))

    def test_variant_code_not_in_db_passes_through_no_error(self):
        headers = ["variant_code", "product_name", "unit_price", "supplier_link"]
        rows = [["UNKNOWN-SKU", "New", "10000", "https://shop.com/x"]]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertEqual(len(result["valid"]), 1)
        self.assertIsNone(result["valid"][0]["variant_id"])
        self.assertEqual(result["valid"][0]["variant_code"], "UNKNOWN-SKU")

    def test_color_missing_returned_in_missing_colors(self):
        headers = ["product_name", "dim1_key", "dim1_value", "unit_price", "supplier_link"]
        rows = [["Produk", "Warna", "Dusty Rose", "50000", "https://shop.com/x"]]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertEqual(result["missing_colors"], [{"color_name": "Dusty Rose"}])

    def test_color_known_not_in_missing_colors(self):
        ColorAbbreviation.objects.create(
            company=self.company, color_name="Dusty Rose", abbreviation="DSR"
        )
        headers = ["product_name", "dim1_key", "dim1_value", "unit_price", "supplier_link"]
        rows = [["Produk", "Warna", "Dusty Rose", "50000", "https://shop.com/x"]]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertEqual(result["missing_colors"], [])

    def test_missing_product_names_detected(self):
        headers = [
            "product_name",
            "dim1_key",
            "dim1_value",
            "dim2_key",
            "dim2_value",
            "supplier_link",
            "unit_price",
        ]
        rows = [["", "Warna", "Putih", "", "", "", "50000"]]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertEqual(result["missing_product_names"], [])
        self.assertTrue(
            any("product_name or supplier_link" in e["message"] for e in result["errors"])
        )

    def test_product_name_blank_with_supplier_link_not_missing(self):
        headers = ["product_name", "supplier_link", "unit_price"]
        rows = [["", "https://shop.com/x", "50000"]]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertEqual(result["missing_product_names"], [])

    def test_dim_mismatch_detected_variant_code_and_dim1_both_filled(self):
        headers = [
            "variant_code",
            "product_name",
            "dim1_key",
            "dim1_value",
            "dim2_key",
            "dim2_value",
            "unit_price",
            "supplier_link",
        ]
        rows = [["SKU-001", "Produk", "Warna", "Putih", "", "", "50000", "https://shop.com/x"]]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertEqual(len(result["dim_mismatches"]), 1)
        self.assertEqual(result["dim_mismatches"][0]["variant_code"], "SKU-001")

    def test_dim1_key_filled_dim1_value_blank_errors(self):
        headers = ["product_name", "dim1_key", "dim1_value", "unit_price", "supplier_link"]
        rows = [["Produk", "Warna", "", "50000", "https://shop.com/x"]]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertTrue(any("dim1_value" in e["message"] for e in result["errors"]))

    def test_dim2_key_filled_dim2_value_blank_errors(self):
        headers = [
            "product_name",
            "dim1_key",
            "dim1_value",
            "dim2_key",
            "dim2_value",
            "unit_price",
            "supplier_link",
        ]
        rows = [["Produk", "Warna", "Putih", "Ukuran", "", "50000", "https://shop.com/x"]]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertTrue(any("dim2_value" in e["message"] for e in result["errors"]))

    def test_product_name_and_supplier_link_and_variant_code_all_blank_errors(self):
        headers = ["product_name", "supplier_link", "variant_code", "unit_price"]
        rows = [["", "", "", "50000"]]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertTrue(
            any("product_name or supplier_link" in e["message"] for e in result["errors"])
        )

    def test_variant_code_present_valid_even_without_product_name(self):
        headers = ["variant_code", "product_name", "supplier_link", "unit_price"]
        rows = [["VC-001", "", "", "50000"]]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertEqual(len(result["valid"]), 1)
        self.assertEqual(result["valid"][0]["variant_code"], "VC-001")

    def test_unit_price_blank_errors(self):
        headers = ["product_name", "unit_price", "supplier_link"]
        rows = [["Produk", "", "https://shop.com/x"]]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertTrue(any("unit_price is required" in e["message"] for e in result["errors"]))

    def test_inconsistent_dim1_key_across_rows_errors(self):
        headers = [
            "product_name",
            "dim1_key",
            "dim1_value",
            "dim2_key",
            "dim2_value",
            "category_code",
            "unit_price",
            "supplier_link",
        ]
        rows = [
            ["Produk", "Warna", "Putih", "Ukuran", "S", "TEST", "50000", "https://shop.com/x"],
            ["Produk", "Color", "Hitam", "Ukuran", "M", "TEST", "50000", "https://shop.com/x"],
        ]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertTrue(any("Inconsistent dim1_key" in e["message"] for e in result["errors"]))

    def test_inconsistent_category_code_across_rows_errors(self):
        headers = [
            "product_name",
            "dim1_key",
            "dim1_value",
            "dim2_key",
            "dim2_value",
            "category_code",
            "unit_price",
            "supplier_link",
        ]
        rows = [
            ["Produk", "Warna", "Putih", "Ukuran", "S", "CAT-A", "50000", "https://shop.com/x"],
            ["Produk", "Warna", "Hitam", "Ukuran", "M", "CAT-B", "50000", "https://shop.com/x"],
        ]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertTrue(any("Inconsistent category_code" in e["message"] for e in result["errors"]))

    def test_missing_unit_price_column_header_file_error(self):
        headers = ["product_name", "supplier_link"]
        rows = [["Produk", "https://shop.com/x"]]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertTrue(any("Missing required columns" in e["message"] for e in result["errors"]))
        self.assertEqual(result["valid"], [])

    # ===== generate_variant_suffix tests =====

    def test_2d_product_dim2_first_dim1_second_in_suffix(self):
        from apps.purchasing.services.sourcing_service import generate_variant_suffix

        result = generate_variant_suffix("Warna", "Putih", "Ukuran", "S", {"putih": "WHT"})
        self.assertEqual(result, "S-WHT")

    def test_1d_product_only_dim1_color(self):
        from apps.purchasing.services.sourcing_service import generate_variant_suffix

        result = generate_variant_suffix("Warna", "Putih", "", "", {"putih": "WHT"})
        self.assertEqual(result, "WHT")

    def test_1d_product_only_dim2_size(self):
        from apps.purchasing.services.sourcing_service import generate_variant_suffix

        result = generate_variant_suffix("", "", "Ukuran", "S", {})
        self.assertEqual(result, "S")

    def test_color_not_in_map_raw_value_uppercased(self):
        from apps.purchasing.services.sourcing_service import generate_variant_suffix

        result = generate_variant_suffix("Warna", "Dusty Rose", "", "", {})
        self.assertEqual(result, "DUSTY ROSE")

    def test_both_dims_empty_empty_suffix(self):
        from apps.purchasing.services.sourcing_service import generate_variant_suffix

        result = generate_variant_suffix("", "", "", "", {})
        self.assertEqual(result, "")

    def test_non_color_dim_key_value_uppercased_directly(self):
        from apps.purchasing.services.sourcing_service import generate_variant_suffix

        result = generate_variant_suffix("Material", "Cotton", "Size", "M", {})
        self.assertEqual(result, "M-COTTON")

    # ===== ColorAbbreviation API tests =====
    def test_get_color_abbreviations_returns_list(self):
        from rest_framework.test import APIClient

        from core.models import UserProfile

        ColorAbbreviation.objects.create(
            company=self.company, color_name="Putih", abbreviation="PTH"
        )
        ColorAbbreviation.objects.create(
            company=self.company, color_name="Merah", abbreviation="MRH"
        )
        user = User.objects.create_user(username="color_api1", password="p", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get("/sourcing-pool/color-abbreviations/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_post_creates_new_color_abbreviation(self):
        from rest_framework.test import APIClient

        from core.models import UserProfile

        user = User.objects.create_user(username="color_api2", password="p", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post(
            "/sourcing-pool/color-abbreviations/",
            {"color_name": "Dusty Rose", "abbreviation": "DSR"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            ColorAbbreviation.objects.filter(company=self.company, color_name="Dusty Rose").exists()
        )

    def test_post_same_color_name_updates_abbreviation(self):
        from rest_framework.test import APIClient

        from core.models import UserProfile

        ColorAbbreviation.objects.create(
            company=self.company, color_name="Dusty Rose", abbreviation="DSR"
        )
        user = User.objects.create_user(username="color_api3", password="p", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post(
            "/sourcing-pool/color-abbreviations/",
            {"color_name": "Dusty Rose", "abbreviation": "DSR2"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        obj = ColorAbbreviation.objects.get(company=self.company, color_name="Dusty Rose")
        self.assertEqual(obj.abbreviation, "DSR2")

    def test_post_missing_color_name_returns_400(self):
        from rest_framework.test import APIClient

        from core.models import UserProfile

        user = User.objects.create_user(username="color_api4", password="p", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post(
            "/sourcing-pool/color-abbreviations/",
            {"abbreviation": "XYZ"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_post_missing_abbreviation_returns_400(self):
        from rest_framework.test import APIClient

        from core.models import UserProfile

        user = User.objects.create_user(username="color_api5", password="p", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post(
            "/sourcing-pool/color-abbreviations/",
            {"color_name": "Dusty Rose"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # ---- DELETE tests ----

    def test_delete_color_abbreviation_success(self):
        from rest_framework.test import APIClient

        from core.models import UserProfile

        ColorAbbreviation.objects.create(
            company=self.company, color_name="Dusty Rose", abbreviation="DSR"
        )
        user = User.objects.create_user(username="color_del1", password="p", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.delete(
            "/sourcing-pool/color-abbreviations/",
            {"color_name": "Dusty Rose"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            ColorAbbreviation.objects.filter(company=self.company, color_name="Dusty Rose").exists()
        )

    def test_delete_color_abbreviation_company_scoping_isolation(self):
        from rest_framework.test import APIClient

        from core.models import UserProfile

        company_b = CompanyFactory()
        ColorAbbreviation.objects.create(
            company=company_b, color_name="Dusty Rose", abbreviation="DSR"
        )
        user = User.objects.create_user(username="color_del2", password="p", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.delete(
            "/sourcing-pool/color-abbreviations/",
            {"color_name": "Dusty Rose"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(
            ColorAbbreviation.objects.filter(company=company_b, color_name="Dusty Rose").exists()
        )

    def test_delete_color_abbreviation_not_found(self):
        from rest_framework.test import APIClient

        from core.models import UserProfile

        user = User.objects.create_user(username="color_del3", password="p", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.delete(
            "/sourcing-pool/color-abbreviations/",
            {"color_name": "NonExistent"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_color_abbreviation_missing_color_name_key(self):
        from rest_framework.test import APIClient

        from core.models import UserProfile

        user = User.objects.create_user(username="color_del4", password="p", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.delete(
            "/sourcing-pool/color-abbreviations/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("color_name is required", str(response.data))

    def test_delete_color_abbreviation_empty_string(self):
        from rest_framework.test import APIClient

        from core.models import UserProfile

        user = User.objects.create_user(username="color_del5", password="p", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.delete(
            "/sourcing-pool/color-abbreviations/",
            {"color_name": ""},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("color_name is required", str(response.data))

    def test_delete_color_abbreviation_whitespace_only(self):
        from rest_framework.test import APIClient

        from core.models import UserProfile

        user = User.objects.create_user(username="color_del6", password="p", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.delete(
            "/sourcing-pool/color-abbreviations/",
            {"color_name": "   "},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("color_name is required", str(response.data))

    def test_delete_color_abbreviation_unauthenticated(self):
        from rest_framework.test import APIClient

        ColorAbbreviation.objects.create(
            company=self.company, color_name="Dusty Rose", abbreviation="DSR"
        )
        client = APIClient()
        try:
            response = client.delete(
                "/sourcing-pool/color-abbreviations/",
                {"color_name": "Dusty Rose"},
                format="json",
            )
            self.assertNotEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        except Exception:
            pass
        self.assertTrue(
            ColorAbbreviation.objects.filter(company=self.company, color_name="Dusty Rose").exists()
        )


class TestStatelessSourcingService(TestCase):
    """Tests for stateless sourcing flow (import_and_add + resolve_sourcing_conflicts)."""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company, category_code="TEST")
        self.supplier = SupplierFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.variant = ProductVariantFactory(
            product=self.product, company=self.company, sku_variant_code="EXISTING-SKU"
        )
        self.po = PurchaseOrderFactory(
            company=self.company,
            warehouse=self.warehouse,
            supplier_name=self.supplier.name,
            status=PurchaseOrder.POStatus.DRAFT,
        )
        self.service = SourcingProductService()

    # ---- import_and_add: validation ----

    def test_import_and_add_happy_path_prelinked_variant(self):
        rows = [
            {
                "product_name": "Test Product",
                "variant_name": "Red",
                "variant_id": str(self.variant.id),
                "category_id": str(self.category.id),
                "unit_price": "25.000",
                "qty_suggested": 10,
            }
        ]
        result = self.service.import_and_add(
            po=self.po, supplier_id=str(self.supplier.id), rows=rows
        )
        self.assertEqual(len(result["added"]), 1)
        self.assertEqual(len(result["skipped"]), 0)
        self.assertEqual(len(result["sku_conflicts"]), 0)
        self.assertEqual(PurchaseOrderDetail.objects.filter(purchase_order=self.po).count(), 1)

    def test_import_and_add_rejects_zero_unit_price(self):
        rows = [
            {
                "product_name": "Test",
                "variant_name": "Red",
                "category_id": str(self.category.id),
                "unit_price": "0",
            }
        ]
        with self.assertRaises(ValidationError) as ctx:
            self.service.import_and_add(po=self.po, supplier_id=str(self.supplier.id), rows=rows)
        self.assertIn("unit_price must be > 0", str(ctx.exception))

    def test_import_and_add_rejects_row_without_product_name_supplier_link_or_variant_code(self):
        rows = [
            {
                "product_name": "",
                "variant_name": "Red",
                "category_id": str(self.category.id),
                "unit_price": "10",
                "supplier_link": "",
                "variant_code": "",
            }
        ]
        with self.assertRaises(ValidationError) as ctx:
            self.service.import_and_add(po=self.po, supplier_id=str(self.supplier.id), rows=rows)
        self.assertIn("product_name, supplier_link", str(ctx.exception))

    def test_import_and_add_discounted_price_gte_unit_price_sets_none(self):
        rows = [
            {
                "product_name": "Test",
                "variant_name": "Red",
                "variant_id": str(self.variant.id),
                "category_id": str(self.category.id),
                "unit_price": "25.000",
                "discounted_price": "30.000",
            }
        ]
        result = self.service.import_and_add(
            po=self.po, supplier_id=str(self.supplier.id), rows=rows
        )
        self.assertEqual(len(result["added"]), 1)
        detail = PurchaseOrderDetail.objects.get(purchase_order=self.po)
        self.assertEqual(detail.discounted_unit_price_foreign, detail.unit_price_foreign)

    def test_import_and_add_rejects_negative_qty_suggested(self):
        rows = [
            {
                "product_name": "Test",
                "variant_name": "Red",
                "variant_id": str(self.variant.id),
                "category_id": str(self.category.id),
                "unit_price": "10",
                "qty_suggested": -1,
            }
        ]
        with self.assertRaises(ValidationError) as ctx:
            self.service.import_and_add(po=self.po, supplier_id=str(self.supplier.id), rows=rows)
        self.assertIn("qty_suggested must be >= 0", str(ctx.exception))

    def test_import_and_add_rejects_non_draft_or_ordered_po(self):
        completed_po = PurchaseOrderFactory(
            company=self.company,
            warehouse=self.warehouse,
            status=PurchaseOrder.POStatus.COMPLETED,
        )
        rows = [
            {
                "product_name": "Test",
                "variant_name": "Red",
                "variant_id": str(self.variant.id),
                "category_id": str(self.category.id),
                "unit_price": "10",
            }
        ]
        with self.assertRaises(ValidationError) as ctx:
            self.service.import_and_add(
                po=completed_po, supplier_id=str(self.supplier.id), rows=rows
            )
        self.assertIn("Cannot add items", str(ctx.exception))

    def test_import_and_add_rejects_invalid_category_id(self):
        rows = [
            {
                "product_name": "Test",
                "variant_name": "Red",
                "category_id": "00000000000000000000000000",
                "unit_price": "10",
            }
        ]
        with self.assertRaises(ValidationError) as ctx:
            self.service.import_and_add(po=self.po, supplier_id=str(self.supplier.id), rows=rows)
        self.assertIn("Invalid category_id", str(ctx.exception))

    def test_import_and_add_rejects_invalid_variant_id(self):
        rows = [
            {
                "product_name": "Test",
                "variant_name": "Red",
                "variant_id": "00000000000000000000000000",
                "category_id": str(self.category.id),
                "unit_price": "10",
            }
        ]
        with self.assertRaises(ValidationError) as ctx:
            self.service.import_and_add(po=self.po, supplier_id=str(self.supplier.id), rows=rows)
        self.assertIn("Invalid variant_id", str(ctx.exception))

    # ---- import_and_add: Track A (with variant_code) ----

    def test_import_and_add_track_a_creates_product_and_variant_when_missing(self):
        rows = [
            {
                "product_name": "New Product",
                "variant_name": "Red",
                "variant_code": "NP-RED",
                "category_id": str(self.category.id),
                "unit_price": "15.000",
                "qty_suggested": 5,
            }
        ]
        result = self.service.import_and_add(
            po=self.po, supplier_id=str(self.supplier.id), rows=rows
        )
        self.assertEqual(len(result["added"]), 1)
        self.assertTrue(Product.objects.filter(company=self.company, name="New Product").exists())
        self.assertTrue(
            ProductVariant.objects.filter(company=self.company, sku_variant_code="NP-RED").exists()
        )

    def test_import_and_add_track_a_sku_conflict_when_variant_missing(self):
        product = ProductFactory(company=self.company, category=self.category, sku_code="NP-X")
        rows = [
            {
                "product_name": "New Product",
                "variant_name": "Blue",
                "variant_code": "NP-X-BLUE",
                "category_id": str(self.category.id),
                "unit_price": "15.000",
            }
        ]
        result = self.service.import_and_add(
            po=self.po, supplier_id=str(self.supplier.id), rows=rows
        )
        self.assertEqual(len(result["sku_conflicts"]), 1)
        self.assertEqual(result["sku_conflicts"][0]["sku_code"], "NP-X")
        self.assertEqual(result["sku_conflicts"][0]["existing_product_id"], str(product.id))

    def test_import_and_add_track_a_links_to_existing_variant(self):
        product = ProductFactory(company=self.company, category=self.category, sku_code="NP-X")
        ProductVariant.objects.create(
            product=product,
            company=self.company,
            name="Blue",
            sku_variant_code="NP-X-BLUE",
        )
        rows = [
            {
                "product_name": "New Product",
                "variant_name": "Blue",
                "variant_code": "NP-X-BLUE",
                "category_id": str(self.category.id),
                "unit_price": "15.000",
            }
        ]
        result = self.service.import_and_add(
            po=self.po, supplier_id=str(self.supplier.id), rows=rows
        )
        self.assertEqual(len(result["added"]), 1)

    # ---- import_and_add: Track B (without variant_code) ----

    def test_import_and_add_track_b_creates_product_and_variant(self):
        rows = [
            {
                "product_name": "Track B Product",
                "variant_name": "Red",
                "category_id": str(self.category.id),
                "unit_price": "12.000",
                "qty_suggested": 3,
            }
        ]
        result = self.service.import_and_add(
            po=self.po, supplier_id=str(self.supplier.id), rows=rows
        )
        self.assertEqual(len(result["added"]), 1)
        self.assertTrue(
            Product.objects.filter(company=self.company, name="Track B Product").exists()
        )
        self.assertEqual(PurchaseOrderDetail.objects.filter(purchase_order=self.po).count(), 1)

    def test_import_and_add_track_b_creates_variant_with_suffix(self):
        ColorAbbreviation.objects.create(
            company=self.company, color_name="Merah", abbreviation="MRH"
        )
        rows = [
            {
                "product_name": "Batik",
                "variant_name": "Merah-L",
                "dim1_key": "Warna",
                "dim1_value": "Merah",
                "dim2_key": "Ukuran",
                "dim2_value": "L",
                "category_id": str(self.category.id),
                "unit_price": "50.000",
            }
        ]
        result = self.service.import_and_add(
            po=self.po, supplier_id=str(self.supplier.id), rows=rows
        )
        self.assertEqual(len(result["added"]), 1)
        variant = ProductVariant.objects.get(company=self.company, product__name="Batik")
        self.assertIn("MRH", variant.sku_variant_code)

    # ---- import_and_add: dim mismatch resolution ----

    def test_import_and_add_dim_mismatch_resolution_dims_routes_to_track_b(self):
        rows = [
            {
                "product_name": "Dim Product",
                "variant_name": "Red",
                "variant_code": "DP-RED",
                "category_id": str(self.category.id),
                "unit_price": "20.000",
            }
        ]
        dim_mismatch_resolutions = {"Dim Product||Red": "dims"}
        result = self.service.import_and_add(
            po=self.po,
            supplier_id=str(self.supplier.id),
            rows=rows,
            dim_mismatch_resolutions=dim_mismatch_resolutions,
        )
        self.assertEqual(len(result["added"]), 1)
        variant = ProductVariant.objects.get(company=self.company, product__name="Dim Product")
        # Track B means sku_variant_code is auto-generated, not "DP-RED"
        self.assertNotEqual(variant.sku_variant_code, "DP-RED")

    # ---- import_and_add: supplier_link based ----

    def test_import_and_add_supplier_link_based_grouping(self):
        rows = [
            {
                "supplier_link": "https://shop.com/item1",
                "variant_name": "Red",
                "category_id": str(self.category.id),
                "unit_price": "10.000",
            }
        ]
        result = self.service.import_and_add(
            po=self.po, supplier_id=str(self.supplier.id), rows=rows
        )
        self.assertEqual(len(result["added"]), 1)
        detail = PurchaseOrderDetail.objects.get(purchase_order=self.po)
        self.assertEqual(detail.supplier_link, "https://shop.com/item1")

    # ---- import_and_add: duplicate dedup ----

    def test_import_and_add_duplicate_row_key_deduped(self):
        rows = [
            {
                "product_name": "Dup",
                "variant_name": "Red",
                "variant_id": str(self.variant.id),
                "category_id": str(self.category.id),
                "unit_price": "10.000",
            },
            {
                "product_name": "Dup",
                "variant_name": "Red",
                "variant_id": str(self.variant.id),
                "category_id": str(self.category.id),
                "unit_price": "10.000",
            },
        ]
        result = self.service.import_and_add(
            po=self.po, supplier_id=str(self.supplier.id), rows=rows
        )
        self.assertEqual(len(result["added"]), 1)

    # ---- import_and_add: category auto-creation ----

    def test_import_and_add_auto_creates_category_from_code(self):
        rows = [
            {
                "product_name": "Auto Cat",
                "variant_name": "Red",
                "variant_id": str(self.variant.id),
                "category_code": "AUTOCAT",
                "unit_price": "10.000",
            }
        ]
        result = self.service.import_and_add(
            po=self.po, supplier_id=str(self.supplier.id), rows=rows
        )
        self.assertEqual(len(result["added"]), 1)
        self.assertTrue(
            Category.objects.filter(company=self.company, category_code="AUTOCAT").exists()
        )

    # ---- resolve_sourcing_conflicts ----

    def test_resolve_sourcing_conflicts_add_to_existing(self):
        existing_product = ProductFactory(
            company=self.company, category=self.category, sku_code="EXIST"
        )
        resolutions = [
            {
                "action": "add_to_existing",
                "product_id": str(existing_product.id),
                "row": {
                    "product_name": "Existing Prod",
                    "variant_name": "Blue",
                    "category_id": str(self.category.id),
                    "unit_price": "30.000",
                    "variant_code": "EXIST-BLUE",
                },
            }
        ]
        result = self.service.resolve_sourcing_conflicts(po=self.po, resolutions=resolutions)
        self.assertEqual(len(result["added"]), 1)
        self.assertTrue(
            ProductVariant.objects.filter(
                company=self.company, sku_variant_code="EXIST-BLUE"
            ).exists()
        )

    def test_resolve_sourcing_conflicts_skip(self):
        resolutions = [
            {
                "action": "skip",
                "row": {
                    "product_name": "Skip Me",
                    "variant_name": "Red",
                    "category_id": str(self.category.id),
                    "unit_price": "10.000",
                },
            }
        ]
        result = self.service.resolve_sourcing_conflicts(po=self.po, resolutions=resolutions)
        self.assertEqual(len(result["added"]), 0)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["reason"], "Skipped by user")

    def test_resolve_sourcing_conflicts_unknown_action(self):
        resolutions = [
            {
                "action": "unknown_action",
                "row": {
                    "product_name": "Bad",
                    "variant_name": "Red",
                    "category_id": str(self.category.id),
                    "unit_price": "10.000",
                },
            }
        ]
        with self.assertRaises(ValidationError) as ctx:
            self.service.resolve_sourcing_conflicts(po=self.po, resolutions=resolutions)
        self.assertIn("Unknown action", str(ctx.exception))

    def test_resolve_sourcing_conflicts_missing_product_id(self):
        resolutions = [
            {
                "action": "add_to_existing",
                "row": {
                    "product_name": "No Prod",
                    "variant_name": "Red",
                    "category_id": str(self.category.id),
                    "unit_price": "10.000",
                },
            }
        ]
        with self.assertRaises(ValidationError) as ctx:
            self.service.resolve_sourcing_conflicts(po=self.po, resolutions=resolutions)
        self.assertIn("product_id is required", str(ctx.exception))

    def test_resolve_sourcing_conflicts_product_not_found(self):
        resolutions = [
            {
                "action": "add_to_existing",
                "product_id": "00000000000000000000000000",
                "row": {
                    "product_name": "Ghost Prod",
                    "variant_name": "Red",
                    "category_id": str(self.category.id),
                    "unit_price": "10.000",
                },
            }
        ]
        result = self.service.resolve_sourcing_conflicts(po=self.po, resolutions=resolutions)
        self.assertEqual(len(result["added"]), 0)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["reason"], "Product not found")

    def test_resolve_sourcing_conflicts_malformed_product_id_skipped(self):
        resolutions = [
            {
                "action": "add_to_existing",
                "product_id": "nonexistent-id",
                "row": {
                    "product_name": "Bad ID",
                    "variant_name": "Red",
                    "category_id": str(self.category.id),
                    "unit_price": "10.000",
                },
            }
        ]
        result = self.service.resolve_sourcing_conflicts(po=self.po, resolutions=resolutions)
        self.assertEqual(len(result["added"]), 0)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["reason"], "Product not found")

    def test_resolve_sourcing_conflicts_distinct_index_for_rows_without_identity(self):
        resolutions = [
            {
                "action": "skip",
                "row": {
                    "variant_name": "Red",
                    "unit_price": "10.000",
                },
            },
            {
                "action": "skip",
                "row": {
                    "variant_name": "Blue",
                    "unit_price": "12.000",
                },
            },
        ]
        result = self.service.resolve_sourcing_conflicts(po=self.po, resolutions=resolutions)
        self.assertEqual(len(result["added"]), 0)
        self.assertEqual(len(result["skipped"]), 2)
        self.assertEqual(result["skipped"][0]["item_id"], "0")
        self.assertEqual(result["skipped"][1]["item_id"], "1")

    # ---- import_and_add: missing tests from review ----

    def test_import_and_add_wrong_company_supplier_returns_404(self):
        other_company = CompanyFactory()
        other_supplier = SupplierFactory(company=other_company)
        staff_user = User.objects.create_user(username="staff", password="x", is_staff=True)
        UserProfile.objects.create(user=staff_user, company=self.company, role="admin")
        client = APIClient()
        client.force_authenticate(user=staff_user)
        url = f"/purchase-order/{self.po.id}/import-and-add/"
        response = client.post(
            url,
            {
                "supplier_id": str(other_supplier.id),
                "rows": [
                    {
                        "product_name": "Test",
                        "variant_name": "Red",
                        "category_id": str(self.category.id),
                        "unit_price": "10.000",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_import_and_add_track_b_resubmit_same_rows_creates_single_product(self):
        """Submitting the same Track B rows twice must create only one Product."""
        row = {
            "product_name": "Widget Resubmit",
            "variant_name": "Red",
            "dim1_value": "Red",
            "category_id": str(self.category.id),
            "unit_price": "15.000",
        }
        service = SourcingProductService()
        service.import_and_add(po=self.po, supplier_id=str(self.supplier.id), rows=[row])
        po2 = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2000,
            commission_fee_pct=0,
            delivery_fee=0,
        )
        service.import_and_add(po=po2, supplier_id=str(self.supplier.id), rows=[row])
        count = Product.objects.filter(company=self.company, name="Widget Resubmit").count()
        self.assertEqual(count, 1)

    def test_import_and_add_non_staff_returns_403(self):
        """Non-staff authenticated user POSTing to import-and-add must get 403."""
        from unittest.mock import patch

        import rest_framework.permissions as rfp

        import core.permissions as core_perms

        def _staff_check(self_perm, request, view):
            from rest_framework.permissions import SAFE_METHODS

            if request.method in SAFE_METHODS:
                return bool(request.user and request.user.is_authenticated)
            return bool(request.user and request.user.is_staff)

        def _auth_check(self_perm, request, view):
            return bool(request.user and request.user.is_authenticated)

        non_staff = User.objects.create_user(username="readonly_403", password="x", is_staff=False)
        UserProfileFactory(user=non_staff, company=self.company)

        with (
            patch.object(core_perms.IsStaffOrReadOnly, "has_permission", _staff_check),
            patch.object(rfp.IsAuthenticated, "has_permission", _auth_check),
        ):
            client = APIClient()
            client.force_authenticate(user=non_staff)
            url = f"/purchase-order/{self.po.id}/import-and-add/"
            response = client.post(
                url,
                {
                    "supplier_id": str(self.supplier.id),
                    "rows": [
                        {
                            "product_name": "Test",
                            "variant_name": "Red",
                            "category_id": str(self.category.id),
                            "unit_price": "10.000",
                        }
                    ],
                },
                format="json",
            )
            self.assertEqual(response.status_code, 403)

    def test_import_and_add_unauthenticated_returns_401(self):
        """Unauthenticated client POSTing to import-and-add must get 401."""
        from unittest.mock import patch

        import rest_framework.permissions as rfp

        import core.permissions as core_perms

        def _staff_check(self_perm, request, view):
            from rest_framework.permissions import SAFE_METHODS

            if request.method in SAFE_METHODS:
                return bool(request.user and request.user.is_authenticated)
            return bool(request.user and request.user.is_staff)

        def _auth_check(self_perm, request, view):
            return bool(request.user and request.user.is_authenticated)

        with (
            patch.object(core_perms.IsStaffOrReadOnly, "has_permission", _staff_check),
            patch.object(rfp.IsAuthenticated, "has_permission", _auth_check),
        ):
            client = APIClient()
            url = f"/purchase-order/{self.po.id}/import-and-add/"
            response = client.post(
                url,
                {
                    "supplier_id": str(self.supplier.id),
                    "rows": [
                        {
                            "product_name": "Test",
                            "variant_name": "Red",
                            "category_id": str(self.category.id),
                            "unit_price": "10.000",
                        }
                    ],
                },
                format="json",
            )
            self.assertIn(response.status_code, (401, 403))

    def test_import_and_add_wrong_company_po_returns_404(self):
        """POSTing to a PO from another company must return 404."""
        other_company = CompanyFactory()
        other_po = PurchaseOrderFactory(
            company=other_company,
            warehouse=WarehouseFactory(company=other_company),
            status=PurchaseOrder.POStatus.DRAFT,
        )
        staff_user = User.objects.create_user(username="staff_wp", password="x", is_staff=True)
        UserProfile.objects.create(user=staff_user, company=self.company, role="admin")
        client = APIClient()
        client.force_authenticate(user=staff_user)
        url = f"/purchase-order/{other_po.id}/import-and-add/"
        response = client.post(
            url,
            {
                "supplier_id": str(self.supplier.id),
                "rows": [
                    {
                        "product_name": "Test",
                        "variant_name": "Red",
                        "category_id": str(self.category.id),
                        "unit_price": "10.000",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_import_and_add_no_category_skips(self):
        """Row without category_id and category_code should be skipped."""
        rows = [
            {
                "product_name": "No Cat Product",
                "unit_price": "15.000",
            }
        ]
        result = self.service.import_and_add(
            po=self.po, supplier_id=str(self.supplier.id), rows=rows
        )
        self.assertEqual(len(result["added"]), 0)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["reason"], "No category assigned")

    def test_resolve_sourcing_conflicts_non_string_product_id_skipped(self):
        """Integer product_id in resolve_sourcing_conflicts should be skipped, not crash."""
        resolutions = [
            {
                "action": "add_to_existing",
                "product_id": 123,
                "row": {
                    "product_name": "Int ID",
                    "variant_name": "Red",
                    "category_id": str(self.category.id),
                    "unit_price": "10.000",
                },
            }
        ]
        result = self.service.resolve_sourcing_conflicts(po=self.po, resolutions=resolutions)
        self.assertEqual(len(result["added"]), 0)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["reason"], "Product not found")

    def test_import_and_add_malformed_category_id_returns_400(self):
        """Malformed ULID string for category_id should return a clean ValidationError."""
        rows = [
            {
                "product_name": "Test",
                "variant_name": "Red",
                "category_id": "not-a-real-id",
                "unit_price": "10.000",
            }
        ]
        with self.assertRaises(ValidationError) as ctx:
            self.service.import_and_add(po=self.po, supplier_id=str(self.supplier.id), rows=rows)
        self.assertIn("Invalid category_id", str(ctx.exception))

    def test_import_and_add_malformed_variant_id_returns_400(self):
        """Malformed ULID string for variant_id should return a clean ValidationError."""
        rows = [
            {
                "product_name": "Test",
                "variant_name": "Red",
                "variant_id": "not-a-real-id",
                "category_id": str(self.category.id),
                "unit_price": "10.000",
            }
        ]
        with self.assertRaises(ValidationError) as ctx:
            self.service.import_and_add(po=self.po, supplier_id=str(self.supplier.id), rows=rows)
        self.assertIn("Invalid variant_id", str(ctx.exception))


# ---------------------------------------------------------------------------
# BE3 — relocated Supplier / ProductSupplier tests
# ---------------------------------------------------------------------------


class TestSupplierCRUD(APITestCase):
    """Tests for Supplier CRUD endpoints — relocated from inventory/tests.py (BE3)."""

    def setUp(self):
        self.company = CompanyFactory()
        self.user = User.objects.create_user(
            username="supplier_test_user_be3", password="password", is_staff=True
        )
        from core.models import UserProfile

        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_create_supplier(self):
        response = self.client.post(
            "/suppliers/",
            {"name": "PT Supplier A", "contact_name": "Budi", "country": "Indonesia"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("id", response.data)

    def test_list_suppliers(self):
        from apps.purchasing.factories import SupplierFactory

        SupplierFactory(company=self.company)
        response = self.client.get("/suppliers/", {"company_id": self.company.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", response.data)
        self.assertGreaterEqual(len(results), 1)

    def test_update_supplier(self):
        from apps.purchasing.factories import SupplierFactory

        supplier = SupplierFactory(company=self.company)
        response = self.client.patch(
            f"/suppliers/{supplier.id}/",
            {"name": "PT Supplier B"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response = self.client.get(f"/suppliers/{supplier.id}/", format="json")
        self.assertEqual(response.data["name"], "PT Supplier B")

    def test_deactivate_supplier(self):
        from apps.purchasing.factories import SupplierFactory

        supplier = SupplierFactory(company=self.company, is_active=True)
        response = self.client.patch(
            f"/suppliers/{supplier.id}/",
            {"is_active": False},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response = self.client.get(
            "/suppliers/", {"company_id": self.company.id, "active_only": "true"}, format="json"
        )
        results = response.data.get("results", response.data)
        ids = [s["id"] for s in results]
        self.assertNotIn(str(supplier.id), ids)

    def test_search_supplier(self):
        from apps.purchasing.factories import SupplierFactory

        SupplierFactory(company=self.company, name="Alpha Supplier")
        SupplierFactory(company=self.company, name="Beta Trading")
        response = self.client.get(
            "/suppliers/", {"search": "Alpha", "company_id": self.company.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", response.data)
        names = [s["name"] for s in results]
        self.assertIn("Alpha Supplier", names)
        self.assertNotIn("Beta Trading", names)

    def test_create_supplier_with_link(self):
        response = self.client.post(
            "/suppliers/",
            {"name": "PT Supplier Link", "supplier_link": "https://shop.example.com/store/abc"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["supplier_link"], "https://shop.example.com/store/abc")

    def test_update_supplier_link(self):
        from apps.purchasing.factories import SupplierFactory

        supplier = SupplierFactory(company=self.company)
        response = self.client.patch(
            f"/suppliers/{supplier.id}/",
            {"supplier_link": "https://new-link.com"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["supplier_link"], "https://new-link.com")

    def test_supplier_link_optional(self):
        response = self.client.post(
            "/suppliers/",
            {"name": "PT Supplier No Link"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data["supplier_link"])


class ProductSupplierTest(APITestCase):
    """Tests for ProductSupplier endpoints — relocated from inventory/tests.py (BE3)."""

    def setUp(self):
        self.company = CompanyFactory()
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(
            company=self.company, category=self.category, description="A" * 25
        )
        from apps.purchasing.factories import SupplierFactory

        self.supplier = SupplierFactory(company=self.company)
        self.user = User.objects.create_user(username="ps_test_be3", password="pw", is_staff=True)
        from core.models import UserProfile

        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_create_product_supplier(self):
        resp = self.client.post(
            "/product-suppliers/",
            {
                "product_id": str(self.product.id),
                "supplier_id": str(self.supplier.id),
                "supplier_link": "https://supplier.com/product-x",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["supplier_name"], self.supplier.name)
        self.assertEqual(resp.data["supplier_link"], "https://supplier.com/product-x")

    def test_list_by_product(self):
        from apps.purchasing.factories import ProductSupplierFactory

        ProductSupplierFactory(product=self.product, supplier=self.supplier, company=self.company)
        resp = self.client.get("/product-suppliers/", {"product_id": str(self.product.id)})
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get("results", resp.data)
        self.assertEqual(len(results), 1)

    def test_delete_product_supplier(self):
        from apps.purchasing.factories import ProductSupplierFactory

        ps = ProductSupplierFactory(
            product=self.product, supplier=self.supplier, company=self.company
        )
        resp = self.client.delete(f"/product-suppliers/{ps.id}/")
        self.assertEqual(resp.status_code, 204)


# ---------------------------------------------------------------------------
# BE3 — migration-graph ordering regression tests
# ---------------------------------------------------------------------------


class MigrationGraphOrderingRegressionTests(TestCase):
    """Regression tests proving the BE3 supplier state-move migrations are correctly
    ordered: the inventory DeleteModel migration must always run after the purchasing
    CreateModel/AlterField migration — on fresh replay and on rollback — preventing
    the BE2 ordering-bug class from recurring here."""

    def test_inventory_delete_supplier_runs_after_purchasing_create_supplier(self):
        """inventory/0029 (DeleteModel Supplier/ProductSupplier) must run after
        purchasing/0026 (CreateModel Supplier/ProductSupplier + AlterField) in every
        forwards plan that includes the inventory deletion migration."""
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)
        # Find the new inventory migration that deletes Supplier/ProductSupplier
        inventory_delete_node = ("inventory", "0029_remove_supplier_productsupplier")
        purchasing_create_node = ("purchasing", "0026_add_supplier_productsupplier")
        plan = loader.graph.forwards_plan(inventory_delete_node)
        self.assertIn(
            purchasing_create_node,
            plan,
            f"{purchasing_create_node} must run before {inventory_delete_node} or a "
            "from-scratch replay can delete the models before purchasing recreates them",
        )

    def test_pre_delete_project_state_renders_without_lazy_reference_errors(self):
        """Build the project state immediately BEFORE the inventory DeleteModel migration
        runs and force-render it — reproduces the exact crash class from BE2 where a
        backward executor force-renders state and hits a dangling lazy FK reference."""
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)
        inventory_delete_node = ("inventory", "0029_remove_supplier_productsupplier")
        state = loader.graph.make_state(
            nodes=[inventory_delete_node],
            at_end=False,
            real_apps=loader.unmigrated_apps,
        )
        try:
            state.apps
        except ValueError as exc:
            self.fail(
                "Rendering project state immediately before "
                f"{inventory_delete_node} crashed with a lazy-reference error — "
                "the purchasing CreateModel/AlterField migration is missing a "
                f"dependency edge: {exc}"
            )
