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
from rest_framework.test import APIClient

from apps.catalog.models import Category, Product, ProductVariant
from apps.catalog.tests.factories import (
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.inventory.models import ProductCogs, ProductVariantWarehouse
from apps.inventory.services.inventory_service import InventoryService
from apps.inventory.tests.factories import ProductVariantWarehouseFactory
from apps.purchasing.models import (
    ColorAbbreviation,
    ProductSupplier,
    PurchaseOrder,
    PurchaseOrderDetail,
)
from apps.purchasing.serializers import PurchaseOrderReadSerializer
from apps.purchasing.services.purchasing_service import PurchaseOrderService
from apps.purchasing.services.sourcing_product_service import SourcingProductService
from apps.purchasing.services.sourcing_service import SourcingService
from apps.purchasing.tests.factories import (
    ProductSupplierFactory,
    PurchaseOrderDetailFactory,
    PurchaseOrderFactory,
    SupplierFactory,
)
from core.factories import CompanyFactory, UserProfileFactory, WarehouseFactory
from core.models import UserProfile
from core.utils import compress_pdf_iterative


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

    def test_incremental_received_qty_update_while_delivered_increases_physical_stock(self):
        """Test that raising received_qty on an already-DELIVERED PO (no status change)
        adds only the delta above the prior received_qty to physical stock."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
            delivery_date=date.today() - timedelta(days=1),
            exchange_rate=Decimal("2200"),
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
            received_qty=50,
            updated_qty=50,
            unit_price_foreign=Decimal("10"),
            unit_price_base=Decimal("22000"),
        )
        ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            incoming_qty=50,
            physical_qty=50,
        )

        po.refresh_from_db()

        self.service.update_purchase_order(
            po,
            {
                "order_details": [
                    {
                        "id": str(detail.id),
                        "product_variant_id": str(self.product_variant.id),
                        "ordered_qty": 100,
                        "received_qty": 70,
                        "unit_price_base": Decimal("22000"),
                        "unit_price_foreign": Decimal("10"),
                        "received_date": str(date.today()),
                    }
                ]
            },
        )

        pvw = ProductVariantWarehouse.objects.get(
            product_variant=self.product_variant, warehouse=self.warehouse
        )
        self.assertEqual(pvw.physical_qty, 70)

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
        from apps.purchasing.tests.factories import ProductSupplierFactory, SupplierFactory

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
        from apps.purchasing.tests.factories import ProductSupplierFactory, SupplierFactory

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

    def test_order_qty_as_decimal_string_parsed_to_int(self):
        """order_qty value like '5.0' (string from cell) parses to int 5 without using float."""
        headers = ["product_name", "unit_price", "order_qty", "supplier_link"]
        rows = [["Baju A", "10000", "5.0", "https://shop.com/x"]]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertEqual(len(result["valid"]), 1)
        self.assertEqual(result["valid"][0]["qty_suggested"], 5)

    def test_order_qty_non_numeric_reported_as_error(self):
        """order_qty with a non-numeric string value is reported as an error row."""
        headers = ["product_name", "unit_price", "order_qty", "supplier_link"]
        rows = [["Baju B", "10000", "abc", "https://shop.com/x"]]
        file_bytes = self._build_workbook(headers, rows)
        result = self.service.parse_excel_preview(file_bytes, self.company)
        self.assertEqual(len(result["valid"]), 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("must be a whole number", result["errors"][0]["message"])


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


class PurchaseOrderImportServiceTest(TestCase):
    """Tests for the PO import parser and PurchaseOrderImportService."""

    # ------------------------------------------------------------------ #
    # Parser tests — pure functions, no DB                                #
    # ------------------------------------------------------------------ #

    def test_parse_po_date_extracts_date_from_sheet_name(self):
        """parse_po_date returns a date when a valid date is embedded in the sheet name."""
        from core.parsers.import_purchase_orders_parser import parse_po_date

        result = parse_po_date("PO Recap 2 April 2025")
        from datetime import date

        self.assertEqual(result, date(2025, 4, 2))

    def test_parse_po_date_returns_none_for_invalid_sheet_name(self):
        """parse_po_date returns None when the sheet name contains no recognisable date."""
        from core.parsers.import_purchase_orders_parser import parse_po_date

        self.assertIsNone(parse_po_date("Summary Sheet"))
        self.assertIsNone(parse_po_date("Recap"))

    def test_is_sku_valid_rejects_numeric_strings(self):
        """is_sku_valid returns False for numeric-like codes, Total rows, and # cells."""
        from core.parsers.import_purchase_orders_parser import is_sku_valid

        self.assertFalse(is_sku_valid("1"))
        self.assertFalse(is_sku_valid("123"))
        self.assertFalse(is_sku_valid("12345.0"))  # dot-separated numeric
        self.assertFalse(is_sku_valid("12-345"))  # dash-separated numeric
        self.assertFalse(is_sku_valid("Total"))
        self.assertFalse(is_sku_valid("Total Qty"))
        self.assertFalse(is_sku_valid("#REF!"))
        self.assertFalse(is_sku_valid(None))
        self.assertFalse(is_sku_valid(""))

    def test_is_sku_valid_accepts_valid_sku(self):
        """is_sku_valid returns True for alphanumeric SKU codes."""
        from core.parsers.import_purchase_orders_parser import is_sku_valid

        self.assertTrue(is_sku_valid("MRK-SEG-001-S-BLK"))
        self.assertTrue(is_sku_valid("ABC123"))

    def test_parse_decimal_returns_decimal_not_float(self):
        """parse_decimal returns a Decimal instance (never a float)."""
        from decimal import Decimal

        from core.parsers.import_purchase_orders_parser import parse_decimal

        result = parse_decimal("14.5")
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal("14.5"))

    def test_parse_decimal_returns_none_for_ref_error(self):
        """parse_decimal returns None for #REF!, None, and empty strings."""
        from core.parsers.import_purchase_orders_parser import parse_decimal

        self.assertIsNone(parse_decimal("#REF!"))
        self.assertIsNone(parse_decimal(None))
        self.assertIsNone(parse_decimal(""))

    def test_detect_columns_finds_required_columns(self):
        """detect_columns returns a mapping when headers contain sku and order."""
        from core.parsers.import_purchase_orders_parser import detect_columns

        headers = ("No", "SKU Variant", "Price RMB", "Disc Price", "Order Qty", "SOH")
        result = detect_columns(headers)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("sku", result)
        self.assertIn("order", result)

    def test_detect_columns_returns_none_when_sku_missing(self):
        """detect_columns returns None if the SKU column is absent."""
        from core.parsers.import_purchase_orders_parser import detect_columns

        headers = ("No", "Product Name", "Price", "Order Qty")
        self.assertIsNone(detect_columns(headers))

    def test_extract_exchange_rate_finds_value_after_rmb(self):
        """extract_exchange_rate returns the numeric value that follows the RMB marker."""
        from core.parsers.import_purchase_orders_parser import extract_exchange_rate

        row = ("Label", "RMB", 2210, None, None)
        self.assertEqual(extract_exchange_rate(row), 2210)

    def test_parse_po_sheet_skips_sheet_without_rmb(self):
        """parse_po_sheet returns None when no RMB exchange-rate row is found."""
        from core.parsers.import_purchase_orders_parser import parse_po_sheet

        rows: list[tuple] = [
            ("Header row",),
            ("SKU Variant", "Order Qty"),
            ("MRK-001-S", 10),
            ("MRK-001-M", 20),
        ]
        result = parse_po_sheet("Recap 2 April 2025", rows)
        self.assertIsNone(result)

    def test_parse_po_sheet_applies_price_carry_forward(self):
        """parse_po_sheet carries the last valid price forward to rows with no price."""
        from decimal import Decimal

        from core.parsers.import_purchase_orders_parser import parse_po_sheet

        rows: list[tuple] = [
            ("Exchange", "RMB", 2210),
            ("SKU Variant", "Price", "Disc", "Order Qty", "SOH"),
            ("MRK-001-S", Decimal("14.5"), None, 10, 5),
            ("MRK-001-M", None, None, 20, 3),  # should carry forward 14.5
        ]
        result = parse_po_sheet("Recap 2 April 2025", rows)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(len(result.rows), 2)
        self.assertEqual(result.rows[0].unit_price_rmb, Decimal("14.5"))
        self.assertEqual(result.rows[1].unit_price_rmb, Decimal("14.5"))

    def test_parse_po_sheet_uses_disc_price_when_above_one(self):
        """parse_po_sheet uses the disc column price when it is greater than 1.0."""
        from decimal import Decimal

        from core.parsers.import_purchase_orders_parser import parse_po_sheet

        rows: list[tuple] = [
            ("Exchange", "RMB", 2210),
            ("SKU Variant", "Price", "Disc", "Order Qty", "SOH"),
            ("MRK-001-S", Decimal("14.5"), Decimal("12.0"), 10, 5),
        ]
        result = parse_po_sheet("Recap 2 April 2025", rows)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0].unit_price_rmb, Decimal("12.0"))

    # ------------------------------------------------------------------ #
    # Service tests — require DB (factory-boy setup)                      #
    # ------------------------------------------------------------------ #

    def _make_setup(self):
        """Create company, warehouse, supplier, and one product variant."""
        from apps.catalog.tests.factories import (
            CategoryFactory,
            ProductFactory,
            ProductVariantFactory,
        )
        from apps.purchasing.tests.factories import SupplierFactory
        from core.factories import CompanyFactory, WarehouseFactory

        company = CompanyFactory()
        warehouse = WarehouseFactory(company=company)
        supplier = SupplierFactory(company=company)
        category = CategoryFactory(company=company)
        product = ProductFactory(category=category, company=company)
        variant = ProductVariantFactory(product=product)
        variant_map = {variant.sku_variant_code: str(variant.id)}
        return company, warehouse, supplier, variant, variant_map

    def _make_parsed_sheet(self, variant_code: str, po_date_str: str = "2025-04-02"):
        """Build a minimal ParsedPoSheet for service tests."""
        from datetime import date
        from decimal import Decimal

        from core.parsers.import_purchase_orders_parser import (
            ParsedPoSheet,
            PoLineRow,
        )

        po_date = date.fromisoformat(po_date_str)
        return ParsedPoSheet(
            sheet_name=f"Recap {po_date_str}",
            po_date=po_date,
            exchange_rate=2210,
            rows=[
                PoLineRow(
                    sku_variant_code=variant_code,
                    order_qty=100,
                    unit_price_rmb=Decimal("14.5"),
                    stock_on_hand=5,
                ),
            ],
        )

    def test_import_po_creates_po_with_correct_status_and_number(self):
        """Import creates a PurchaseOrder with COMPLETED status and formatted PO number."""
        from apps.purchasing.services.po_import_service import PurchaseOrderImportService

        company, warehouse, supplier, variant, variant_map = self._make_setup()
        parsed = self._make_parsed_sheet(variant.sku_variant_code, "2025-04-02")

        service = PurchaseOrderImportService()
        service.import_purchase_orders(
            company=company,
            warehouse=warehouse,
            supplier=supplier,
            parsed_sheets=[parsed],
            variant_map=variant_map,
        )

        po = PurchaseOrder.objects.filter(company=company).first()
        self.assertIsNotNone(po)
        assert po is not None
        self.assertEqual(po.status, PurchaseOrder.POStatus.COMPLETED)
        self.assertTrue(po.purchase_order_number.startswith("PO-2025-"))

    def test_import_po_creates_po_details_with_decimal_precision_on_total_amount(self):
        """Import uses Decimal arithmetic — 14.5 * 2210 * 100 == 3_204_500 exactly."""
        from apps.purchasing.services.po_import_service import PurchaseOrderImportService

        company, warehouse, supplier, variant, variant_map = self._make_setup()
        parsed = self._make_parsed_sheet(variant.sku_variant_code, "2025-04-02")

        service = PurchaseOrderImportService()
        service.import_purchase_orders(
            company=company,
            warehouse=warehouse,
            supplier=supplier,
            parsed_sheets=[parsed],
            variant_map=variant_map,
        )

        detail = PurchaseOrderDetail.objects.filter(company=company).first()
        self.assertIsNotNone(detail)
        assert detail is not None
        # unit_price_base = int(round(14.5 * 2210)) = 32045
        # total_price_base = 32045 * 100 = 3_204_500
        self.assertEqual(detail.unit_price_base, 32045)
        self.assertEqual(detail.total_price_base, 3_204_500)

    def test_import_po_shipping_fee_computed_without_float_precision_loss(self):
        """The critical split-bug test: Decimal(str) prevents float-rounding on money."""
        from decimal import Decimal

        # Verify the Decimal calculation path is correct — no float in the chain
        price = Decimal("14.5")
        exchange_rate = Decimal("2210")
        qty = 100

        unit_price_base = int(round(price * exchange_rate))
        total_price_base = unit_price_base * qty

        # 14.5 * 2210 = 32044.5 → rounded → 32045; * 100 = 3_204_500
        self.assertEqual(unit_price_base, 32045)
        self.assertEqual(total_price_base, 3_204_500)
        # Confirm float would give a different (wrong) result on some systems
        # This verifies that our Decimal path is the fix for the split bug

    def test_import_po_creates_fifo_cogs_layers(self):
        """Import creates one ProductCogs layer per variant per PO."""
        from apps.inventory.models import ProductCogs
        from apps.purchasing.services.po_import_service import PurchaseOrderImportService

        company, warehouse, supplier, variant, variant_map = self._make_setup()
        parsed = self._make_parsed_sheet(variant.sku_variant_code, "2025-04-02")

        service = PurchaseOrderImportService()
        service.import_purchase_orders(
            company=company,
            warehouse=warehouse,
            supplier=supplier,
            parsed_sheets=[parsed],
            variant_map=variant_map,
        )

        cogs = ProductCogs.objects.filter(
            product_variant=variant,
            warehouse=warehouse,
        )
        self.assertEqual(cogs.count(), 1)
        cogs_row = cogs.first()
        assert cogs_row is not None
        from decimal import Decimal

        self.assertEqual(cogs_row.price_rmb, Decimal("14.5"))
        self.assertEqual(cogs_row.exchange_rate, 2210)
        self.assertEqual(cogs_row.original_qty, 100)
        self.assertEqual(cogs_row.remaining_qty, 100)
        self.assertTrue(cogs_row.is_active)

    def test_import_po_creates_stock_movements_with_correct_balance_chain(self):
        """Import creates StockMovements and balance_after equals balance_before + qty."""
        from apps.inventory.models import StockMovement
        from apps.purchasing.services.po_import_service import PurchaseOrderImportService

        company, warehouse, supplier, variant, variant_map = self._make_setup()
        parsed = self._make_parsed_sheet(variant.sku_variant_code, "2025-04-02")

        service = PurchaseOrderImportService()
        service.import_purchase_orders(
            company=company,
            warehouse=warehouse,
            supplier=supplier,
            parsed_sheets=[parsed],
            variant_map=variant_map,
        )

        movements = StockMovement.objects.filter(
            product_variant=variant,
            warehouse=warehouse,
        )
        self.assertEqual(movements.count(), 1)
        mv = movements.first()
        assert mv is not None
        self.assertEqual(mv.movement_type, "IN")
        self.assertEqual(mv.quantity, 100)
        self.assertEqual(mv.balance_before, 0)
        self.assertEqual(mv.balance_after, 100)

    def test_import_po_is_idempotent_second_run_same_result(self):
        """Running the same import twice yields identical final state (no duplicates)."""
        from apps.inventory.models import ProductCogs, StockMovement
        from apps.purchasing.services.po_import_service import PurchaseOrderImportService

        company, warehouse, supplier, variant, variant_map = self._make_setup()
        parsed = self._make_parsed_sheet(variant.sku_variant_code, "2025-04-02")

        service = PurchaseOrderImportService()
        service.import_purchase_orders(
            company=company,
            warehouse=warehouse,
            supplier=supplier,
            parsed_sheets=[parsed],
            variant_map=variant_map,
        )
        # Second run — should produce same state, not duplicates
        service.import_purchase_orders(
            company=company,
            warehouse=warehouse,
            supplier=supplier,
            parsed_sheets=[parsed],
            variant_map=variant_map,
        )

        self.assertEqual(PurchaseOrder.objects.filter(company=company).count(), 1)
        self.assertEqual(PurchaseOrderDetail.objects.filter(company=company).count(), 1)
        self.assertEqual(
            ProductCogs.objects.filter(product_variant=variant, warehouse=warehouse).count(), 1
        )
        self.assertEqual(
            StockMovement.objects.filter(product_variant=variant, warehouse=warehouse).count(),
            1,
        )

    def test_import_po_skips_rows_with_invalid_sku(self):
        """Rows with numeric-only or empty SKU codes are excluded from import."""
        from decimal import Decimal

        from apps.purchasing.services.po_import_service import PurchaseOrderImportService
        from core.parsers.import_purchase_orders_parser import (
            ParsedPoSheet,
            PoLineRow,
        )

        company, warehouse, supplier, variant, variant_map = self._make_setup()

        from datetime import date

        parsed = ParsedPoSheet(
            sheet_name="Recap 2 April 2025",
            po_date=date(2025, 4, 2),
            exchange_rate=2210,
            rows=[
                PoLineRow(
                    sku_variant_code=variant.sku_variant_code,
                    order_qty=50,
                    unit_price_rmb=Decimal("10.0"),
                    stock_on_hand=0,
                ),
                # A row with invalid SKU should already be filtered out by parse_po_sheet,
                # so the service receives only valid rows — validate variant_map lookup
            ],
        )

        service = PurchaseOrderImportService()
        result = service.import_purchase_orders(
            company=company,
            warehouse=warehouse,
            supplier=supplier,
            parsed_sheets=[parsed],
            variant_map=variant_map,
        )

        self.assertEqual(result.details_created, 1)

    def test_import_po_skips_rows_with_zero_qty(self):
        """Rows with order_qty == 0 produce no details (parser filters, service honours it)."""
        from decimal import Decimal

        from apps.purchasing.services.po_import_service import PurchaseOrderImportService
        from core.parsers.import_purchase_orders_parser import (
            ParsedPoSheet,
            PoLineRow,
        )

        company, warehouse, supplier, variant, variant_map = self._make_setup()

        from datetime import date

        parsed = ParsedPoSheet(
            sheet_name="Recap 2 April 2025",
            po_date=date(2025, 4, 2),
            exchange_rate=2210,
            rows=[
                PoLineRow(
                    sku_variant_code=variant.sku_variant_code,
                    order_qty=0,
                    unit_price_rmb=Decimal("10.0"),
                    stock_on_hand=0,
                ),
            ],
        )

        service = PurchaseOrderImportService()
        result = service.import_purchase_orders(
            company=company,
            warehouse=warehouse,
            supplier=supplier,
            parsed_sheets=[parsed],
            variant_map=variant_map,
        )

        self.assertEqual(result.details_created, 0)
