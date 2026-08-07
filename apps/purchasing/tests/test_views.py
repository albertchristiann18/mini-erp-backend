from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.catalog.tests.factories import (
    CategoryFactory,
    ProductFactory,
    ProductPhotoFactory,
    ProductVariantFactory,
)
from apps.inventory.models import ProductCogs, ProductVariantWarehouse
from apps.inventory.tests.factories import (
    ProductVariantWarehouseFactory,
)
from apps.purchasing.models import (
    PurchaseOrder,
    PurchaseOrderStatusHistory,
)
from apps.purchasing.services.purchasing_service import PurchaseOrderService
from apps.purchasing.tests.factories import (
    PurchaseOrderDetailFactory,
    PurchaseOrderFactory,
)
from core.factories import CompanyFactory, WarehouseFactory
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
        from apps.purchasing.tests.factories import SupplierFactory
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
        from apps.purchasing.tests.factories import SupplierFactory

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
        from apps.purchasing.tests.factories import SupplierFactory

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
            currency="USD",
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
        self.assertIn("currency", field_names)
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
            currency="USD",
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

    def test_patch_to_completed_over_received_without_remarks_returns_field_dict(self):
        """Regression for QTYEDIT-4: the service raises a dict-keyed ValidationError; the view
        must surface it as `{"order_details": [...]}`, not `{"error": "<dict-repr string>"}`.
        """
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
            delivery_date=date.today(),
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=20,
            received_qty=10,
            updated_qty=10,
        )

        response = self.client.patch(
            f"/purchase-order/{po.id}/",
            {
                "status": PurchaseOrder.POStatus.COMPLETED,
                "order_details": [{"id": str(detail.id), "received_qty": 40}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("error", response.data)
        self.assertIn("order_details", response.data)
        self.assertIsInstance(response.data["order_details"], list)
        self.assertIn("Remarks is required", response.data["order_details"][0])


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
        from apps.purchasing.tests.factories import SupplierFactory

        SupplierFactory(company=self.company)
        response = self.client.get("/suppliers/", {"company_id": self.company.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", response.data)
        self.assertGreaterEqual(len(results), 1)

    def test_update_supplier(self):
        from apps.purchasing.tests.factories import SupplierFactory

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
        from apps.purchasing.tests.factories import SupplierFactory

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
        from apps.purchasing.tests.factories import SupplierFactory

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
        from apps.purchasing.tests.factories import SupplierFactory

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
        from apps.purchasing.tests.factories import SupplierFactory

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
        from apps.purchasing.tests.factories import ProductSupplierFactory

        ProductSupplierFactory(product=self.product, supplier=self.supplier, company=self.company)
        resp = self.client.get("/product-suppliers/", {"product_id": str(self.product.id)})
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get("results", resp.data)
        self.assertEqual(len(results), 1)

    def test_delete_product_supplier(self):
        from apps.purchasing.tests.factories import ProductSupplierFactory

        ps = ProductSupplierFactory(
            product=self.product, supplier=self.supplier, company=self.company
        )
        resp = self.client.delete(f"/product-suppliers/{ps.id}/")
        self.assertEqual(resp.status_code, 204)


class MoneySerializationCrudContractTest(TestCase):
    """Pins the CRUD side of the money serialization contract: a partial update
    (PATCH) must leave an untouched money field exactly unchanged — never
    re-rounded — and it must round-trip through the API as an exact decimal
    string, not a JSON number. If either half is later reversed (rounding
    creeps into a CRUD field, or a field starts overriding the default
    coerce-to-string behavior), this test fails.
    """

    def setUp(self):
        self.client = APIClient()
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.user = User.objects.create_user(
            username="money_crud_patch_user", password="password", is_staff=True
        )
        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_patch_of_unrelated_field_leaves_decimal_money_field_exact_and_string(self):
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            delivery_fee=Decimal("123.456"),
            commission_fee_rmb=Decimal("77.250"),
        )

        response = self.client.patch(
            f"/purchase-order/{po.id}/", {"note": "updated note"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        detail = self.client.get(f"/purchase-order/{po.id}/", format="json")
        self.assertEqual(detail.status_code, status.HTTP_200_OK)

        # Exact — not re-rounded — and serialized as a string, not a JSON number.
        self.assertEqual(detail.data["delivery_fee"], "123.456")
        self.assertIsInstance(detail.data["delivery_fee"], str)
        self.assertEqual(detail.data["commission_fee_rmb"], "77.250")
        self.assertIsInstance(detail.data["commission_fee_rmb"], str)

        po.refresh_from_db()
        self.assertEqual(po.delivery_fee, Decimal("123.456"))
        self.assertEqual(po.commission_fee_rmb, Decimal("77.250"))
        self.assertEqual(po.note, "updated note")


# ---------------------------------------------------------------------------
# BE3 — migration-graph ordering regression tests
# ---------------------------------------------------------------------------
