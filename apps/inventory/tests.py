# apps/inventory/tests/test_api.py
import io
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated as IsAuthenticatedPermission
from rest_framework.test import APIClient, APITestCase

_real_auth_has_permission = IsAuthenticatedPermission.has_permission

from apps.inventory.factories import (
    CategoryFactory,
    ProductCogsFactory,
    ProductDimensionImageFactory,
    ProductFactory,
    ProductPhotoFactory,
    ProductSupplierFactory,
    ProductVariantFactory,
    ProductVariantWarehouseFactory,
    StockMovementFactory,
    SupplierFactory,
)
from apps.inventory.models import (
    Category,
    Product,
    ProductBusinessEntity,
    ProductCogs,
    ProductDimensionImage,
    ProductSupplier,
    ProductVariant,
    ProductVariantWarehouse,
    StockMovement,
)
from apps.inventory.services.inventory_service import InventoryService
from apps.purchasing.factories import PurchaseOrderFactory
from apps.purchasing.models import PurchaseOrder
from core.factories import CompanyFactory, WarehouseFactory
from core.permissions import IsStaffOrReadOnly as StaffPerm

_real_staff_perm = StaffPerm.has_permission


class InventoryAPITest(APITestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.category = CategoryFactory(company=self.company)
        self.base_payload = [
            {
                "company_id": str(self.company.id),
                "category_id": str(self.category.id),
                "name": "Kemeja Batik Pria Premium",
                "description": "Batik Slimfit bahan katun halus, nyaman untuk kerja maupun acara formal.",
                "variant_options": {"warna": ["Navy"], "size": ["L", "XL"]},
                "specifications": {
                    "Merek": "Tidak ada merek",
                    "Bahan": ["Katun", "Bulu Domba"],
                    "Motif": ["Batik", "Kotak-kotak"],
                    "Negara_Asal": "Indonesia",
                },
                "weight": 300,
                "length": 25,
                "width": 20,
                "height": 3,
                "variants": [
                    {
                        "variant_values": {"warna": "Navy", "size": "L"},
                        "sku_variant_code": "NAVY-L",
                        "base_price": 180000,
                    },
                    {
                        "variant_values": {"warna": "Navy", "size": "XL"},
                        "sku_variant_code": "NAVY-XL",
                        "base_price": 185000,
                    },
                ],
            }
        ]

    def test_create_product(self):
        response = self.client.post("/product/", self.base_payload, format="json")
        # Verify result
        self.assertEqual(response.status_code, 201)

        # Check if the mock actually worked
        product = Product.objects.last()
        variants = ProductVariant.objects.filter(product=product)
        self.assertTrue(product.sku_code.startswith(self.category.category_code))
        for v in variants:
            self.assertEqual(v.product_id, product.id)
            self.assertIn("NAVY", v.sku_variant_code)

    def test_create_multiple_product(self):
        payload = self.base_payload + [
            {
                "company_id": str(self.company.id),
                "category_id": str(self.category.id),
                "name": "Kemeja Batik Pria Premium B",
                "description": "Batik Slimfit bahan katun halus, nyaman untuk kerja maupun acara formal.",
                "variant_options": {"warna": ["Blue"], "size": ["L", "XL"]},
                "specifications": {
                    "Merek": "Tidak ada merek",
                    "Bahan": ["Katun", "Bulu Domba"],
                    "Motif": ["Batik", "Kotak-kotak"],
                    "Negara_Asal": "Indonesia",
                },
                "weight": 300,
                "length": 25,
                "width": 20,
                "height": 3,
                "variants": [
                    {
                        "variant_values": {"warna": "Blue", "size": "L"},
                        "sku_variant_code": "BLUE-L",
                        "base_price": 180000,
                    },
                    {
                        "variant_values": {"warna": "Blue", "size": "XL"},
                        "sku_variant_code": "BLUE-XL",
                        "base_price": 185000,
                    },
                ],
            }
        ]
        response = self.client.post("/product/", payload, format="json")
        # Verify result
        self.assertEqual(response.status_code, 201)

        # 1. Verify Database Counts (The most important bulk check)
        self.assertEqual(Product.objects.count(), 2)
        self.assertEqual(ProductVariant.objects.count(), 4)

        # 2. Verify First Product Relationship
        product_a = Product.objects.get(name="Kemeja Batik Pria Premium")
        variants_a = ProductVariant.objects.filter(product=product_a).order_by("name")
        self.assertEqual(variants_a.count(), 2)
        # Check that the SKU correctly combined Parent SKU + Variant values
        self.assertIn(product_a.sku_code, variants_a[0].sku_variant_code)

        # 3. Verify Second Product Relationship (The Global Index Test)
        product_b = Product.objects.get(name="Kemeja Batik Pria Premium B")
        variants_b = ProductVariant.objects.filter(product=product_b).order_by("name")
        self.assertEqual(variants_b.count(), 2)

        # Verify that the variant for Product B is NOT linked to Product A
        # This confirms your global counter logic worked!
        for v in variants_b:
            self.assertEqual(v.product_id, product_b.id)
            self.assertIn("BLUE", v.sku_variant_code)

    def test_create_multiple_products_with_nested_variants_and_listings(self):
        """
        Tests that 2 products with multiple variants
        are correctly mapped and saved in bulk.
        """
        # setup_data would be a fixture or dictionary containing your payload
        payload = self.base_payload + [
            {
                "company_id": str(self.company.id),
                "category_id": str(self.category.id),
                "name": "Kemeja Batik Pria Premium B",
                "description": "Batik Slimfit bahan katun halus, nyaman untuk kerja maupun acara formal.",
                "variant_options": {"warna": ["Blue"], "size": ["L", "XL"]},
                "specifications": {
                    "Merek": "Tidak ada merek",
                    "Bahan": ["Katun", "Bulu Domba"],
                    "Motif": ["Batik", "Kotak-kotak"],
                    "Negara_Asal": "Indonesia",
                },
                "weight": 300,
                "length": 25,
                "width": 20,
                "height": 3,
                "variants": [
                    {
                        "variant_values": {"warna": "Blue", "size": "L"},
                        "sku_variant_code": "BLUE-L",
                        "base_price": 180000,
                    },
                    {
                        "variant_values": {"warna": "Blue", "size": "XL"},
                        "sku_variant_code": "BLUE-XL",
                        "base_price": 185000,
                    },
                ],
            }
        ]
        response = self.client.post("/product/", payload, format="json")
        # 1. Basic Response Check
        assert response.status_code == 201
        # 2. Verify Database Integrity (Counts)
        assert Product.objects.count() == 2
        assert ProductVariant.objects.count() == 4

        # 3. Verify Specific Mapping (Global Indexing Check)
        # Fetch the second product to ensure it didn't get Product A's variants
        prod_b = Product.objects.get(name="Kemeja Batik Pria Premium B")
        variants_b = ProductVariant.objects.filter(product=prod_b)

        assert variants_b.count() == 2
        for variant in variants_b:
            # Verify the variant names match the 'Blue' logic in payload B
            assert "Blue" in variant.name

    def test_create_product_returns_id_and_variant_ids(self):
        """POST /product/ returns {id, name, variants: [{id, name, sku_variant_code}]}"""
        payload = {
            "company_id": str(self.company.id),
            "category_id": str(self.category.id),
            "name": "Test Product Quick",
            "description": "Test Product Quick (created via PO - update description later)",
            "variant_options": {"color": ["Blue"]},
            "variants": [
                {
                    "variant_values": {"color": "Blue"},
                    "sku_variant_code": "BLU-M",
                    "base_price": 50000,
                }
            ],
        }
        response = self.client.post("/product/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertIn("id", response.data)
        self.assertEqual(len(response.data["variants"]), 1)
        product = Product.objects.get(name="Test Product Quick")
        self.assertEqual(
            response.data["variants"][0]["sku_variant_code"], f"{product.sku_code}-BLU-M"
        )
        self.assertEqual(response.data["variants"][0]["name"], "Blue")

    def test_create_product_sets_sku_variant_code_in_db(self):
        """sku_variant_code from request is saved to the ProductVariant record with sku_code prefix."""
        payload = {
            "company_id": str(self.company.id),
            "category_id": str(self.category.id),
            "name": "SKU Test Product",
            "description": "SKU Test Product (created via PO - update description later)",
            "variants": [
                {
                    "variant_values": {"color": "red"},
                    "sku_variant_code": "RED-L",
                    "base_price": 75000,
                }
            ],
        }
        response = self.client.post("/product/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        variant_id = response.data["variants"][0]["id"]
        variant = ProductVariant.objects.get(id=variant_id)
        product = Product.objects.get(name="SKU Test Product")
        self.assertEqual(variant.sku_variant_code, f"{product.sku_code}-RED-L")


class InventoryServiceStockUpdateTest(TestCase):
    """Test cases for InventoryService.update_stock_on_po method"""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.service = InventoryService()

    def test_update_stock_on_po_ordered_status(self):
        """Test stock update when PO status changes to ORDERED."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 0,
                "updated_qty": 0,
            }
        ]

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.ORDERED,
            data=data,
        )

        pvw = ProductVariantWarehouse.objects.get(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
        )
        self.assertEqual(pvw.incoming_qty, 100)
        self.assertEqual(pvw.physical_qty, 0)

        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.total_incoming_qty, 100)

    def test_update_stock_on_po_delivered_first_time(self):
        """Test stock update when PO status changes to DELIVERED (first time)."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
        )

        pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            incoming_qty=100,
            physical_qty=0,
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 50,
                "updated_qty": 0,
            }
        ]

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 50)
        self.assertEqual(pvw.physical_qty, 50)

        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.total_incoming_qty, 50)
        self.assertEqual(self.product_variant.total_available_qty, 50)

    def test_update_stock_on_po_delivered_subsequent(self):
        """Test stock update when PO is already DELIVERED and receives more."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
        )

        pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            incoming_qty=90,
            physical_qty=10,
        )
        self.product_variant.total_incoming_qty = 90
        self.product_variant.total_available_qty = 10
        self.product_variant.save()

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 30,
                "updated_qty": 10,
            }
        ]

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 70)
        self.assertEqual(pvw.physical_qty, 30)

        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.total_incoming_qty, 70)
        self.assertEqual(self.product_variant.total_available_qty, 30)

    def test_update_stock_on_po_with_empty_data(self):
        """Test that empty data does nothing."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.ORDERED,
            data=[],
        )

        self.assertEqual(ProductVariantWarehouse.objects.count(), 0)

    def test_update_stock_on_po_delivered_incoming_qty_decreased(self):
        """Test incoming_qty is adjusted when received_qty decreases from SHIPPED to DELIVERED."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
        )

        pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            incoming_qty=100,
            physical_qty=0,
        )
        self.product_variant.total_incoming_qty = 100
        self.product_variant.save()

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 50,
                "updated_qty": 0,
            }
        ]

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 50)
        self.assertEqual(pvw.physical_qty, 50)

        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.total_incoming_qty, 50)
        self.assertEqual(self.product_variant.total_available_qty, 50)

    def test_update_stock_on_po_delivered_received_qty_decreased(self):
        """Test physical_qty decreases when received_qty is decreased on subsequent delivery."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
        )

        pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            incoming_qty=50,
            physical_qty=50,
        )
        self.product_variant.total_incoming_qty = 50
        self.product_variant.total_available_qty = 50
        self.product_variant.save()

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 30,
                "updated_qty": 50,
            }
        ]

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 70)
        self.assertEqual(pvw.physical_qty, 30)

        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.total_incoming_qty, 70)
        self.assertEqual(self.product_variant.total_available_qty, 30)

    def test_update_stock_on_po_received_qty_exceeds_ordered(self):
        """Test incoming_qty becomes 0 when received_qty exceeds ordered_qty."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
        )

        pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            incoming_qty=10,
            physical_qty=0,
        )
        self.product_variant.total_incoming_qty = 10
        self.product_variant.save()

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 10,
                "received_qty": 15,
                "updated_qty": 0,
            }
        ]

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 0)
        self.assertEqual(pvw.physical_qty, 15)

        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.total_incoming_qty, 0)
        self.assertEqual(self.product_variant.total_available_qty, 15)

    def test_update_stock_on_po_full_delivery(self):
        """Test incoming_qty becomes 0 when fully received."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
        )

        pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            incoming_qty=100,
            physical_qty=0,
        )
        self.product_variant.total_incoming_qty = 100
        self.product_variant.save()

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 100,
                "updated_qty": 0,
            }
        ]

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 0)
        self.assertEqual(pvw.physical_qty, 100)

        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.total_incoming_qty, 0)
        self.assertEqual(self.product_variant.total_available_qty, 100)

    def test_update_stock_on_po_no_changes_received_qty_same(self):
        """Test that stock is not changed when received_qty is the same as updated_qty."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
        )

        pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            incoming_qty=50,
            physical_qty=50,
        )
        self.product_variant.total_incoming_qty = 50
        self.product_variant.total_available_qty = 50
        self.product_variant.save()

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 50,
                "updated_qty": 50,
            }
        ]

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 50)
        self.assertEqual(pvw.physical_qty, 50)

        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.total_incoming_qty, 50)
        self.assertEqual(self.product_variant.total_available_qty, 50)

    def test_update_stock_on_po_completed_clears_incoming(self):
        """Test that COMPLETED status clears remaining incoming_qty."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
        )

        pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            incoming_qty=20,
            physical_qty=80,
        )
        self.product_variant.total_incoming_qty = 20
        self.product_variant.total_available_qty = 80
        self.product_variant.save()

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 80,
                "updated_qty": 80,
            }
        ]

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.COMPLETED,
            data=data,
        )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 0)
        self.assertEqual(pvw.physical_qty, 80)

        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.total_incoming_qty, 0)
        self.assertEqual(self.product_variant.total_available_qty, 80)

    def test_update_stock_on_po_completed_no_incoming(self):
        """Test that COMPLETED status does nothing when incoming_qty is already 0."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
        )

        pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            incoming_qty=0,
            physical_qty=100,
        )
        self.product_variant.total_incoming_qty = 0
        self.product_variant.total_available_qty = 100
        self.product_variant.save()

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 100,
                "updated_qty": 100,
            }
        ]

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.COMPLETED,
            data=data,
        )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 0)
        self.assertEqual(pvw.physical_qty, 100)

        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.total_incoming_qty, 0)
        self.assertEqual(self.product_variant.total_available_qty, 100)


class InventoryServiceCOGSUpdateTest(TestCase):
    """Test cases for InventoryService.update_cogs_on_po method"""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.service = InventoryService()

    def test_update_cogs_on_po_delivered_first_time(self):
        """Test COGS is created when PO status changes to DELIVERED (first time)."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
        )

        initial_cogs_count = ProductCogs.objects.count()

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 100,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
            }
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        )
        self.assertEqual(cogs.count(), initial_cogs_count + 1)

        cogs_record = cogs.first()
        self.assertIsNotNone(cogs_record)
        self.assertEqual(cogs_record.price_rmb, Decimal("10.0000"))
        self.assertEqual(cogs_record.exchange_rate, 2200)
        self.assertEqual(cogs_record.cogs_amount, 22000)
        self.assertEqual(cogs_record.original_qty, 100)
        self.assertEqual(cogs_record.remaining_qty, 100)

    def test_update_cogs_on_po_with_discount(self):
        """Test COGS uses discounted_unit_price_foreign when provided."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 100,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
                "discounted_unit_price_foreign": Decimal("8"),
            }
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs)
        self.assertEqual(cogs.price_rmb, Decimal("8.0000"))
        self.assertEqual(cogs.cogs_amount, 17600)

    def test_update_cogs_on_po_partial_delivery(self):
        """Test COGS is created with partial received_qty."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 50,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
            }
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs)
        self.assertEqual(cogs.original_qty, 50)
        self.assertEqual(cogs.remaining_qty, 50)
        self.assertEqual(cogs.cogs_amount, 22000)

    def test_update_cogs_on_po_subsequent_delivery(self):
        """Test COGS is updated when received_qty increases on subsequent delivery."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
        )

        ProductCogsFactory(
            company=self.company,
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
            price_rmb=Decimal("10.0000"),
            exchange_rate=2200,
            cogs_amount=22000,
            original_qty=50,
            remaining_qty=50,
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 80,
                "updated_qty": 50,
                "unit_price_foreign": Decimal("10"),
            }
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs)
        self.assertEqual(cogs.original_qty, 80)
        self.assertEqual(cogs.remaining_qty, 80)

    def test_update_cogs_on_po_ordered_status_no_op(self):
        """Test that COGS is not affected when PO status is ORDERED."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )

        initial_cogs_count = ProductCogs.objects.count()

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 0,
                "updated_qty": 0,
            }
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.ORDERED,
            data=data,
        )

        self.assertEqual(ProductCogs.objects.count(), initial_cogs_count)

    def test_update_cogs_on_po_with_empty_data(self):
        """Test that empty data does nothing."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
        )

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=[],
        )

        self.assertEqual(ProductCogs.objects.count(), 0)

    def test_update_cogs_on_po_with_allocated_shipping_and_delivery_fees_single_item(self):
        """Test COGS includes allocated shipping, delivery, and commission fees for single item."""
        product = self.product_variant.product
        product.length = 10
        product.width = 10
        product.height = 10
        product.save()

        shipping_fee_per_cbm = 100000
        cbm = Decimal("0.01")
        shipping_fee = int(shipping_fee_per_cbm * cbm)

        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
            shipping_fee_per_cbm=shipping_fee_per_cbm,
            cbm=cbm,
            shipping_fee=shipping_fee,
            delivery_fee=Decimal("100.5"),
            commission_fee=22000,
            total_item_amount=220000,
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
                "discounted_total_price_base": 220000,
            }
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs)
        self.assertEqual(cogs.original_qty, 10)
        self.assertEqual(cogs.remaining_qty, 10)

        unit_price_idr = 10 * 2200
        # Single item: all fees allocated to this item
        allocated_shipping = shipping_fee  # 1000
        allocated_delivery = int(Decimal("100.5") * 2200)  # 221100
        allocated_commission = 22000
        shipping_per_unit = allocated_shipping / 10
        delivery_per_unit = allocated_delivery / 10
        commission_per_unit = allocated_commission / 10
        expected_cogs = int(
            unit_price_idr + shipping_per_unit + delivery_per_unit + commission_per_unit
        )

        self.assertEqual(cogs.cogs_amount, expected_cogs)
        self.assertEqual(cogs.allocated_shipping_fee, allocated_shipping)
        self.assertEqual(cogs.allocated_delivery_fee, allocated_delivery)
        self.assertEqual(cogs.allocated_commission_fee, allocated_commission)

    def test_update_cogs_on_po_with_allocated_fees_multiple_items_same_volume(self):
        """Test COGS with multiple items sharing shipping fees equally when same LxWxH."""
        product1 = self.product_variant.product
        product1.length = 10
        product1.width = 10
        product1.height = 10
        product1.save()

        product2 = ProductFactory(
            category=self.category, company=self.company, length=10, width=10, height=10
        )
        product_variant2 = ProductVariantFactory(product=product2)
        ProductVariantWarehouseFactory(
            product_variant=product_variant2, warehouse=self.warehouse, company=self.company
        )

        shipping_fee_per_cbm = 100000
        cbm = Decimal("0.01")
        shipping_fee = int(shipping_fee_per_cbm * cbm)

        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
            shipping_fee_per_cbm=shipping_fee_per_cbm,
            cbm=cbm,
            shipping_fee=shipping_fee,
            delivery_fee=Decimal("0"),
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
            },
            {
                "product_variant_id": str(product_variant2.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("20"),
            },
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs1 = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()
        cogs2 = ProductCogs.objects.filter(
            product_variant=product_variant2,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs1)
        self.assertIsNotNone(cogs2)

        volume1 = Decimal("10") * Decimal("10") * Decimal("10") / Decimal("1000000") * Decimal("10")
        volume2 = Decimal("10") * Decimal("10") * Decimal("10") / Decimal("1000000") * Decimal("10")
        total_volume = volume1 + volume2

        allocated_shipping1 = int(shipping_fee * volume1 / total_volume)
        allocated_shipping2 = int(shipping_fee * volume2 / total_volume)

        expected_cogs1 = 10 * 2200 + allocated_shipping1 / 10
        expected_cogs2 = 20 * 2200 + allocated_shipping2 / 10

        self.assertEqual(cogs1.cogs_amount, expected_cogs1)
        self.assertEqual(cogs2.cogs_amount, expected_cogs2)
        self.assertEqual(cogs1.allocated_shipping_fee, allocated_shipping1)
        self.assertEqual(cogs2.allocated_shipping_fee, allocated_shipping2)

    def test_update_cogs_on_po_with_allocated_fees_multiple_items_different_volume(self):
        """Test COGS with multiple items where each takes portion of shipping based on volume."""
        product1 = self.product_variant.product
        product1.length = 10
        product1.width = 10
        product1.height = 10
        product1.save()

        product2 = ProductFactory(
            category=self.category, company=self.company, length=20, width=20, height=20
        )
        product_variant2 = ProductVariantFactory(product=product2)
        ProductVariantWarehouseFactory(
            product_variant=product_variant2, warehouse=self.warehouse, company=self.company
        )

        shipping_fee_per_cbm = 100000
        cbm = Decimal("0.09")
        shipping_fee = int(shipping_fee_per_cbm * cbm)

        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
            shipping_fee_per_cbm=shipping_fee_per_cbm,
            cbm=cbm,
            shipping_fee=shipping_fee,
            delivery_fee=Decimal("0"),
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
            },
            {
                "product_variant_id": str(product_variant2.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("20"),
            },
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs1 = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()
        cogs2 = ProductCogs.objects.filter(
            product_variant=product_variant2,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs1)
        self.assertIsNotNone(cogs2)

        volume1 = Decimal("10") * Decimal("10") * Decimal("10") / Decimal("1000000") * Decimal("10")
        volume2 = Decimal("20") * Decimal("20") * Decimal("20") / Decimal("1000000") * Decimal("10")
        total_volume = volume1 + volume2

        allocated_shipping1 = int(shipping_fee * volume1 / total_volume)
        allocated_shipping2 = int(shipping_fee * volume2 / total_volume)

        expected_cogs1 = 10 * 2200 + allocated_shipping1 / 10
        expected_cogs2 = 20 * 2200 + allocated_shipping2 / 10

        self.assertEqual(cogs1.cogs_amount, expected_cogs1)
        self.assertEqual(cogs2.cogs_amount, expected_cogs2)
        self.assertEqual(cogs1.allocated_shipping_fee, allocated_shipping1)
        self.assertEqual(cogs2.allocated_shipping_fee, allocated_shipping2)

    def test_update_cogs_on_po_received_qty_decreases(self):
        """Test COGS is updated when received_qty decreases."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
        )

        ProductCogsFactory(
            company=self.company,
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
            price_rmb=Decimal("10.0000"),
            exchange_rate=2200,
            cogs_amount=22000,
            original_qty=50,
            remaining_qty=50,
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 30,
                "updated_qty": 50,
                "unit_price_foreign": Decimal("10"),
            }
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs)
        self.assertEqual(cogs.original_qty, 30)
        self.assertEqual(cogs.remaining_qty, 30)

    def test_cogs_cbm_proportional_shipping(self):
        """2 items with different CBM: shipping allocated proportionally by CBM, delivery+commission by value."""
        product1 = self.product_variant.product
        product1.length = 10
        product1.width = 10
        product1.height = 10
        product1.save()

        product2 = ProductFactory(
            category=self.category, company=self.company, length=20, width=20, height=20
        )
        product_variant2 = ProductVariantFactory(product=product2)
        ProductVariantWarehouseFactory(
            product_variant=product_variant2, warehouse=self.warehouse, company=self.company
        )

        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
            shipping_fee=10000,
            shipping_fee_per_cbm=100000,
            cbm=Decimal("0.09"),
            delivery_fee=Decimal("50"),
            commission_fee=22000,
            total_item_amount=660000,
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
                "discounted_total_price_base": 220000,
            },
            {
                "product_variant_id": str(product_variant2.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("20"),
                "discounted_total_price_base": 440000,
            },
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs1 = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()
        cogs2 = ProductCogs.objects.filter(
            product_variant=product_variant2,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs1)
        self.assertIsNotNone(cogs2)

        # CBM: item1 = 10*10*10/1e6 * 10 = 0.01, item2 = 20*20*20/1e6 * 10 = 0.08, total = 0.09
        cbm1 = Decimal("10") * Decimal("10") * Decimal("10") / Decimal("1000000") * Decimal("10")
        cbm2 = Decimal("20") * Decimal("20") * Decimal("20") / Decimal("1000000") * Decimal("10")
        total_cbm = cbm1 + cbm2

        # Shipping: CBM-proportional
        expected_shipping1 = int(round(10000 * cbm1 / total_cbm))
        expected_shipping2 = int(round(10000 * cbm2 / total_cbm))
        self.assertEqual(cogs1.allocated_shipping_fee, expected_shipping1)
        self.assertEqual(cogs2.allocated_shipping_fee, expected_shipping2)

        # Delivery: value-proportional
        total_delivery_idr = Decimal("50") * 2200
        value_ratio1 = Decimal("220000") / Decimal("660000")
        value_ratio2 = Decimal("440000") / Decimal("660000")
        expected_delivery1 = int(round(total_delivery_idr * value_ratio1))
        expected_delivery2 = int(round(total_delivery_idr * value_ratio2))
        self.assertEqual(cogs1.allocated_delivery_fee, expected_delivery1)
        self.assertEqual(cogs2.allocated_delivery_fee, expected_delivery2)

        # Commission: value-proportional
        expected_commission1 = int(round(Decimal("22000") * value_ratio1))
        expected_commission2 = int(round(Decimal("22000") * value_ratio2))
        self.assertEqual(cogs1.allocated_commission_fee, expected_commission1)
        self.assertEqual(cogs2.allocated_commission_fee, expected_commission2)

    def test_cogs_value_proportional_delivery_and_commission(self):
        """2 items with different values: delivery_fee and commission allocated proportionally by item value."""
        product1 = self.product_variant.product
        product1.length = 10
        product1.width = 10
        product1.height = 10
        product1.save()

        product2 = ProductFactory(
            category=self.category, company=self.company, length=10, width=10, height=10
        )
        product_variant2 = ProductVariantFactory(product=product2)
        ProductVariantWarehouseFactory(
            product_variant=product_variant2, warehouse=self.warehouse, company=self.company
        )

        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
            shipping_fee=5000,
            shipping_fee_per_cbm=100000,
            cbm=Decimal("0.02"),
            delivery_fee=Decimal("100"),
            commission_fee=33000,
            total_item_amount=660000,
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
                "discounted_total_price_base": 220000,
            },
            {
                "product_variant_id": str(product_variant2.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("20"),
                "discounted_total_price_base": 440000,
            },
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs1 = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()
        cogs2 = ProductCogs.objects.filter(
            product_variant=product_variant2,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs1)
        self.assertIsNotNone(cogs2)

        # Same CBM, so shipping is split equally
        self.assertEqual(cogs1.allocated_shipping_fee, 2500)
        self.assertEqual(cogs2.allocated_shipping_fee, 2500)

        # Delivery fee: value-proportional
        total_delivery_idr = Decimal("100") * 2200
        expected_delivery1 = int(round(total_delivery_idr * Decimal("220000") / Decimal("660000")))
        expected_delivery2 = int(round(total_delivery_idr * Decimal("440000") / Decimal("660000")))
        self.assertEqual(cogs1.allocated_delivery_fee, expected_delivery1)
        self.assertEqual(cogs2.allocated_delivery_fee, expected_delivery2)

        # Commission: value-proportional
        expected_commission1 = int(round(Decimal("33000") * Decimal("220000") / Decimal("660000")))
        expected_commission2 = int(round(Decimal("33000") * Decimal("440000") / Decimal("660000")))
        self.assertEqual(cogs1.allocated_commission_fee, expected_commission1)
        self.assertEqual(cogs2.allocated_commission_fee, expected_commission2)

    def test_cogs_amount_includes_commission(self):
        """cogs_amount = unit_price_idr + shipping_per_unit + delivery_per_unit + commission_per_unit."""
        product = self.product_variant.product
        product.length = 10
        product.width = 10
        product.height = 10
        product.save()

        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
            shipping_fee=1000,
            shipping_fee_per_cbm=100000,
            cbm=Decimal("0.01"),
            delivery_fee=Decimal("50"),
            commission_fee=22000,
            total_item_amount=220000,
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
                "discounted_total_price_base": 220000,
            },
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs)

        unit_price_idr = Decimal("10") * 2200  # 22000
        shipping_per_unit = Decimal(str(cogs.allocated_shipping_fee)) / Decimal("10")
        delivery_per_unit = Decimal(str(cogs.allocated_delivery_fee)) / Decimal("10")
        commission_per_unit = Decimal(str(cogs.allocated_commission_fee)) / Decimal("10")
        expected_cogs = int(
            unit_price_idr + shipping_per_unit + delivery_per_unit + commission_per_unit
        )

        self.assertEqual(cogs.cogs_amount, expected_cogs)


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

    def test_product_list_scoped_by_company(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get("/product/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [p["id"] for p in response.data["results"]]
        self.assertIn(str(self.product_a.id), ids)
        self.assertNotIn(str(self.product_b.id), ids)

    def test_product_list_includes_inactive(self):
        """Inactive products are visible in the list so staff can reactivate them"""
        inactive = ProductFactory(
            company=self.company_a, category=self.category_a, is_active=False, description="A" * 25
        )
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.get("/product/")
        self.assertEqual(resp.status_code, 200)
        ids = [p["id"] for p in resp.data["results"]]
        self.assertIn(str(inactive.id), ids)

    def test_product_list_filter_by_category(self):
        self.client.force_authenticate(user=self.user_a)
        cat_other = CategoryFactory(company=self.company_a)
        p_other = ProductFactory(company=self.company_a, category=cat_other)
        resp = self.client.get("/product/", {"category": self.category_a.id})
        self.assertEqual(resp.status_code, 200)
        ids = [p["id"] for p in resp.data["results"]]
        self.assertIn(str(self.product_a.id), ids)
        self.assertNotIn(str(p_other.id), ids)

    def test_product_list_ordering_name(self):
        self.client.force_authenticate(user=self.user_a)
        ProductFactory(company=self.company_a, category=self.category_a, name="B")
        ProductFactory(company=self.company_a, category=self.category_a, name="A")
        ProductFactory(company=self.company_a, category=self.category_a, name="C")
        resp = self.client.get("/product/", {"ordering": "name"})
        self.assertEqual(resp.status_code, 200)
        names = [p["name"] for p in resp.data["results"]]
        self.assertEqual(names, sorted(names))

    def test_product_list_ordering_sku_code(self):
        self.client.force_authenticate(user=self.user_a)
        ProductFactory(company=self.company_a, category=self.category_a)
        ProductFactory(company=self.company_a, category=self.category_a)
        resp = self.client.get("/product/", {"ordering": "sku_code"})
        self.assertEqual(resp.status_code, 200)
        codes = [p["sku_code"] for p in resp.data["results"]]
        self.assertEqual(codes, sorted(codes))

    def test_product_list_ordering_invalid_ignored(self):
        self.client.force_authenticate(user=self.user_a)
        ProductFactory(company=self.company_a, category=self.category_a)
        resp = self.client.get("/product/", {"ordering": "malicious; DROP TABLE"})
        self.assertEqual(resp.status_code, 200)

    def test_variant_stock_list_scoped_by_company(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get("/product-variants/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [v["id"] for v in response.data["results"]]
        self.assertIn(str(self.variant_a.id), ids)
        self.assertNotIn(str(self.variant_b.id), ids)


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


class EdgeCaseInventoryTests(TestCase):
    """Tests for edge case fixes in inventory."""

    def setUp(self):
        from django.core.exceptions import ValidationError

        self.ValidationError = ValidationError
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.service = InventoryService()

    # Fix 2: Negative stock guard
    def test_outbound_insufficient_stock_raises_error(self):
        from apps.inventory.models import StockMovement

        ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=5,
        )
        self.product_variant.total_available_qty = 5
        self.product_variant.save()

        with self.assertRaises(self.ValidationError) as ctx:
            self.service.record_single_stock_movement(
                variant_id=self.product_variant.id,
                warehouse_id=self.warehouse.id,
                qty=10,
                movement_type=StockMovement.MovementType.OUTBOUND,
            )
        self.assertIn("Insufficient stock", str(ctx.exception))

    def test_outbound_sufficient_stock_succeeds(self):
        from apps.inventory.models import StockMovement

        pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=10,
        )
        self.product_variant.total_available_qty = 10
        self.product_variant.save()

        self.service.record_single_stock_movement(
            variant_id=self.product_variant.id,
            warehouse_id=self.warehouse.id,
            qty=5,
            movement_type=StockMovement.MovementType.OUTBOUND,
        )
        pvw.refresh_from_db()
        self.assertEqual(pvw.physical_qty, 5)

    # Fix 9: COGS remaining_qty cannot go negative
    def test_cogs_remaining_qty_negative_raises_error(self):
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
        )
        # Create COGS with remaining_qty=5 (some already sold)
        ProductCogsFactory(
            company=self.company,
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
            price_rmb=Decimal("10.0000"),
            exchange_rate=2200,
            cogs_amount=22000,
            original_qty=50,
            remaining_qty=5,
        )

        # Try to decrease received_qty by 10 (but only 5 remaining)
        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 40,
                "updated_qty": 50,
                "unit_price_foreign": Decimal("10"),
            }
        ]

        with self.assertRaises(self.ValidationError) as ctx:
            self.service.update_cogs_on_po(
                po=po,
                new_status=PurchaseOrder.POStatus.DELIVERED,
                data=data,
            )
        self.assertIn("already sold", str(ctx.exception))


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


class UpdateVariantPriceTest(APITestCase):
    """Tests for PATCH /product/{id}/update_variant_price/{variant_id}/"""

    def setUp(self):
        self.company = CompanyFactory()
        self.other_company = CompanyFactory()
        self.staff_user = User.objects.create_user(
            username="staff", password="password", is_staff=True
        )
        self.non_staff_user = User.objects.create_user(
            username="nonstaff", password="password", is_staff=False
        )
        from core.models import UserProfile

        UserProfile.objects.create(user=self.staff_user, company=self.company, role="admin")
        UserProfile.objects.create(user=self.non_staff_user, company=self.company, role="viewer")

        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(company=self.company, category=self.category, is_active=True)
        self.variant = ProductVariantFactory(
            product=self.product, company=self.company, is_active=True, base_price=100000
        )

        self.other_category = CategoryFactory(company=self.other_company)
        self.other_product = ProductFactory(
            company=self.other_company, category=self.other_category, is_active=True
        )
        self.other_variant = ProductVariantFactory(
            product=self.other_product, company=self.other_company, is_active=True, base_price=50000
        )

    def _url(self, product, variant):
        return f"/product/{product.id}/update_variant_price/{variant.id}/"

    def test_update_variant_price_success(self):
        """Staff user can update base_price on a variant belonging to their company's product."""
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.patch(
            self._url(self.product, self.variant),
            {"base_price": 150000},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["base_price"], 150000)
        self.assertEqual(response.data["id"], str(self.variant.id))
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.base_price, 150000)

    def test_update_variant_price_wrong_company(self):
        """Variant belongs to different company product — expect 404."""
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.patch(
            self._url(self.other_product, self.other_variant),
            {"base_price": 150000},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_variant_price_missing_field(self):
        """Body without base_price — expect 400."""
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.patch(
            self._url(self.product, self.variant),
            {},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_variant_price_negative(self):
        """Body with negative base_price — expect 400."""
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.patch(
            self._url(self.product, self.variant),
            {"base_price": -1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_variant_price_non_staff(self):
        """Non-staff user — expect 403."""
        with patch.object(StaffPerm, "has_permission", _real_staff_perm):
            self.client.force_authenticate(user=self.non_staff_user)
            response = self.client.patch(
                self._url(self.product, self.variant),
                {"base_price": 150000},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_update_variant_price_cross_variant_isolation(self):
        """Staff user (company A) tries to update a variant from other_company's product
        using their own product ID in the URL — expect 404."""
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.patch(
            self._url(self.product, self.other_variant),
            {"base_price": 150000},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class SupplierLinkTest(TestCase):
    """Tests for product_supplier_link field on ProductVariantStockSerializer."""

    def setUp(self):
        self.company = CompanyFactory()
        self.category = CategoryFactory(company=self.company)

    def test_variant_stock_serializer_includes_product_supplier_link(self):
        from apps.inventory.serializers import ProductVariantStockSerializer

        product = ProductFactory(
            company=self.company,
            category=self.category,
        )
        variant = ProductVariantFactory(product=product)
        serializer = ProductVariantStockSerializer(variant)
        self.assertIn("product_supplier_link", serializer.data)
        self.assertIsNone(serializer.data["product_supplier_link"])

    def test_variant_stock_serializer_includes_product_photo_url(self):
        """Serialize a ProductVariant, assert product_photo_url key in output."""
        from apps.inventory.serializers import ProductVariantStockSerializer

        product = ProductFactory(
            company=self.company,
            category=self.category,
        )
        variant = ProductVariantFactory(product=product)
        serializer = ProductVariantStockSerializer(variant)
        self.assertIn("product_photo_url", serializer.data)


class TestSupplierCRUD(APITestCase):
    """Tests for Supplier CRUD endpoints"""

    def setUp(self):
        self.company = CompanyFactory()
        self.user = User.objects.create_user(
            username="supplier_test_user", password="password", is_staff=True
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
        from apps.inventory.factories import SupplierFactory

        SupplierFactory(company=self.company)
        response = self.client.get("/suppliers/", {"company_id": self.company.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", response.data)
        self.assertGreaterEqual(len(results), 1)

    def test_update_supplier(self):
        from apps.inventory.factories import SupplierFactory

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
        from apps.inventory.factories import SupplierFactory

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
        from apps.inventory.factories import SupplierFactory

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
        from apps.inventory.factories import SupplierFactory

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


class SaveVariantsTest(APITestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(
            company=self.company, category=self.category, description="A" * 25
        )
        self.user = User.objects.create_user(
            username="savevars_user", password="password", is_staff=True
        )
        from core.models import UserProfile

        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def _url(self, pk):
        return f"/product/{pk}/save_variants/"

    def test_save_variants_creates_new_variants(self):
        """POST save_variants with no id → creates new variants, returns created=2"""
        payload = {
            "variant_options": {"color": ["Red", "Blue"]},
            "variants": [
                {"variant_values": {"color": "Red"}, "sku_variant_code": "", "base_price": 100000},
                {"variant_values": {"color": "Blue"}, "sku_variant_code": "", "base_price": 110000},
            ],
        }
        resp = self.client.post(self._url(self.product.id), payload, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["created"], 2)
        self.assertEqual(resp.data["updated"], 0)
        self.assertEqual(
            ProductVariant.objects.filter(product=self.product, is_active=True).count(), 2
        )
        variants = ProductVariant.objects.filter(product=self.product, is_active=True).order_by(
            "base_price"
        )
        self.assertEqual(variants[0].name, "Red")
        self.assertEqual(variants[1].name, "Blue")

    def test_save_variants_updates_existing_variant(self):
        """POST save_variants with existing variant id → updates base_price"""
        variant = ProductVariantFactory(
            product=self.product, company=self.company, variant_values={"color": "Red"}
        )
        payload = {
            "variant_options": {"color": ["Red"]},
            "variants": [
                {"id": str(variant.id), "variant_values": {"color": "Red"}, "base_price": 999000},
            ],
        }
        resp = self.client.post(self._url(self.product.id), payload, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["updated"], 1)
        variant.refresh_from_db()
        self.assertEqual(variant.base_price, 999000)

    def test_save_variants_deactivates_stale_without_stock(self):
        """Variants not in payload with no stock → deactivated"""
        v1 = ProductVariantFactory(
            product=self.product, company=self.company, variant_values={"color": "Red"}
        )
        v2 = ProductVariantFactory(
            product=self.product, company=self.company, variant_values={"color": "Blue"}
        )
        payload = {
            "variant_options": {"color": ["Red", "Blue"]},
            "variants": [
                {"id": str(v1.id), "variant_values": {"color": "Red"}, "base_price": 100000},
            ],
        }
        resp = self.client.post(self._url(self.product.id), payload, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(str(v2.id), resp.data["deactivated"])
        v2.refresh_from_db()
        self.assertFalse(v2.is_active)

    def test_save_variants_keeps_stale_with_stock(self):
        """Variants not in payload WITH stock → kept active, in kept_with_stock"""
        v1 = ProductVariantFactory(
            product=self.product, company=self.company, variant_values={"color": "Red"}
        )
        v2 = ProductVariantFactory(
            product=self.product,
            company=self.company,
            variant_values={"color": "Blue"},
        )
        ProductVariant.objects.filter(id=v2.id).update(total_incoming_qty=10, total_available_qty=5)
        v2.refresh_from_db()
        payload = {
            "variant_options": {"color": ["Red", "Blue"]},
            "variants": [
                {"id": str(v1.id), "variant_values": {"color": "Red"}, "base_price": 100000},
            ],
        }
        resp = self.client.post(self._url(self.product.id), payload, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(str(v2.id), resp.data["kept_with_stock"])
        v2.refresh_from_db()
        self.assertTrue(v2.is_active)

    def test_save_variants_updates_variant_options(self):
        """variant_options is persisted on the product"""
        opts = {"size": ["S"]}
        payload = {"variant_options": opts, "variants": []}
        self.client.post(self._url(self.product.id), payload, format="json")
        self.product.refresh_from_db()
        self.assertEqual(self.product.variant_options, opts)

    def test_create_product_without_variants(self):
        """POST /product/ with variants=[] returns 201"""
        payload = {
            "company_id": str(self.company.id),
            "category_id": str(self.category.id),
            "name": "No Variant Product",
            "description": "A" * 25,
            "variants": [],
        }
        resp = self.client.post("/product/", payload, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(Product.objects.filter(name="No Variant Product").exists())


class TestVariantFilterBySupplier(APITestCase):
    """Tests for filtering variants by supplier via ProductSupplier"""

    def setUp(self):
        self.company = CompanyFactory()
        self.category = CategoryFactory(company=self.company)
        self.product_a = ProductFactory(category=self.category, company=self.company)
        self.product_b = ProductFactory(category=self.category, company=self.company)
        self.variant_a = ProductVariantFactory(product=self.product_a, company=self.company)
        self.variant_b = ProductVariantFactory(product=self.product_b, company=self.company)
        self.supplier_a = SupplierFactory(company=self.company)
        self.supplier_b = SupplierFactory(company=self.company)
        self.user = User.objects.create_user(
            username="vfilter_test_user", password="password", is_staff=True
        )
        from core.models import UserProfile

        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)
        ProductSupplierFactory(
            product=self.product_a, supplier=self.supplier_a, company=self.company
        )
        ProductSupplierFactory(
            product=self.product_b, supplier=self.supplier_b, company=self.company
        )

    def test_filter_variants_by_supplier(self):
        response = self.client.get(
            "/product-variants/", {"supplier_id": self.supplier_a.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [v["id"] for v in response.data["results"]]
        self.assertIn(str(self.variant_a.id), ids)
        self.assertNotIn(str(self.variant_b.id), ids)


class ProductDetailAPITest(APITestCase):
    """Tests for GET /product/{id}/ — variant detail fields"""

    def setUp(self):
        self.company = CompanyFactory()
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.variant = ProductVariantFactory(
            product=self.product,
            company=self.company,
            current_cogs=50000,
            total_available_qty=120,
            total_incoming_qty=30,
        )
        self.user = User.objects.create_user(
            username="detail_test", password="password", is_staff=True
        )
        from core.models import UserProfile

        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_product_detail_includes_variant_cogs_and_stock_fields(self):
        response = self.client.get(f"/product/{self.product.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        variants = response.data.get("variants", [])
        self.assertGreater(len(variants), 0)
        variant_data = variants[0]
        self.assertIn("current_cogs", variant_data)
        self.assertIn("total_available_qty", variant_data)
        self.assertIn("total_incoming_qty", variant_data)
        self.assertEqual(variant_data["current_cogs"], 50000)
        self.assertEqual(variant_data["total_available_qty"], 120)
        self.assertEqual(variant_data["total_incoming_qty"], 30)


class ProductSupplierTest(APITestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(
            company=self.company, category=self.category, description="A" * 25
        )
        self.supplier = SupplierFactory(company=self.company)
        self.user = User.objects.create_user(username="ps_test", password="pw", is_staff=True)
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
        ProductSupplierFactory(product=self.product, supplier=self.supplier, company=self.company)
        resp = self.client.get("/product-suppliers/", {"product_id": str(self.product.id)})
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get("results", resp.data)
        self.assertEqual(len(results), 1)

    def test_delete_product_supplier(self):
        ps = ProductSupplierFactory(
            product=self.product, supplier=self.supplier, company=self.company
        )
        resp = self.client.delete(f"/product-suppliers/{ps.id}/")
        self.assertEqual(resp.status_code, 204)

    def test_variant_filter_by_supplier_via_product(self):
        """GET /product-variants/?supplier_id=X returns variants whose product is linked to supplier"""
        variant = ProductVariantFactory(product=self.product, company=self.company)
        other_product = ProductFactory(
            company=self.company, category=self.category, description="B" * 25
        )
        other_variant = ProductVariantFactory(product=other_product, company=self.company)
        ProductSupplierFactory(product=self.product, supplier=self.supplier, company=self.company)
        resp = self.client.get("/product-variants/", {"supplier_id": str(self.supplier.id)})
        self.assertEqual(resp.status_code, 200)
        ids = [v["id"] for v in resp.data["results"]]
        self.assertIn(str(variant.id), ids)
        self.assertNotIn(str(other_variant.id), ids)


class CategoryDeleteTest(APITestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.category = CategoryFactory(company=self.company)
        self.staff_user = User.objects.create_user(
            username="cat_staff", password="password", is_staff=True
        )
        self.non_staff_user = User.objects.create_user(
            username="cat_user", password="password", is_staff=False
        )
        from core.models import UserProfile

        UserProfile.objects.create(user=self.staff_user, company=self.company, role="admin")
        UserProfile.objects.create(user=self.non_staff_user, company=self.company, role="viewer")

    def test_delete_category_no_products_succeeds(self):
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.delete(f"/category/{self.category.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Category.objects.filter(id=self.category.id).exists())

    def test_delete_category_with_products_returns_409(self):
        ProductFactory.create_batch(2, category=self.category, company=self.company)
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.delete(f"/category/{self.category.id}/")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("products", response.data)
        self.assertEqual(len(response.data["products"]), 2)
        for product in response.data["products"]:
            self.assertIn("name", product)
            self.assertIn("sku_code", product)
        self.assertIn(
            response.data["products"][0]["sku_code"],
            [product.sku_code for product in Product.objects.filter(category=self.category)],
        )
        self.assertIn(
            response.data["products"][1]["sku_code"],
            [product.sku_code for product in Product.objects.filter(category=self.category)],
        )
        self.assertTrue(Category.objects.filter(id=self.category.id).exists())

    def test_delete_category_non_staff_forbidden(self):
        with patch.object(StaffPerm, "has_permission", _real_staff_perm):
            self.client.force_authenticate(user=self.non_staff_user)
            response = self.client.delete(f"/category/{self.category.id}/")
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Category.objects.filter(id=self.category.id).exists())


class CategoryCompanyScopeTest(APITestCase):
    def setUp(self):
        self.company_a = CompanyFactory()
        self.company_b = CompanyFactory()
        self.category_a1 = CategoryFactory(company=self.company_a)
        self.category_a2 = CategoryFactory(company=self.company_a)
        self.category_b1 = CategoryFactory(company=self.company_b)
        self.category_b2 = CategoryFactory(company=self.company_b)
        self.staff_user = User.objects.create_user(
            username="cat_scope_staff", password="password", is_staff=True
        )
        from core.models import UserProfile

        UserProfile.objects.create(user=self.staff_user, company=self.company_a, role="admin")

    def test_category_list_only_returns_own_company(self):
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.get("/category/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 2)

    def test_category_create_stamps_company(self):
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.post(
            "/category/",
            {"name": "New Cat", "category_code": "NC01", "description": "test"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        category = Category.objects.get(id=response.data["id"])
        self.assertEqual(category.company, self.company_a)

    def test_category_delete_own_company_succeeds(self):
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.delete(f"/category/{self.category_a1.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_category_delete_other_company_returns_404(self):
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.delete(f"/category/{self.category_b1.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class TestBusinessEntityService(TestCase):
    """Tests for BusinessEntityService — service-level business logic."""

    def setUp(self):
        from apps.inventory.factories import (
            BusinessEntityFactory,
            CompanyMarketplaceFactory,
            ProductFactory,
        )
        from apps.inventory.services.business_entity_service import BusinessEntityService

        self.ProductBusinessEntity = ProductBusinessEntity
        self.service = BusinessEntityService()
        self.company = CompanyFactory()
        self.marketplace_shopee = CompanyMarketplaceFactory(company=self.company, name="Shopee")
        self.marketplace_tiktok = CompanyMarketplaceFactory(company=self.company, name="TikTok")

        self.product = ProductFactory(company=self.company, category__company=self.company)

        self.be_shopee_a = BusinessEntityFactory(
            company=self.company, marketplace=self.marketplace_shopee
        )
        self.be_shopee_b = BusinessEntityFactory(
            company=self.company, marketplace=self.marketplace_shopee
        )
        self.be_tiktok = BusinessEntityFactory(
            company=self.company, marketplace=self.marketplace_tiktok
        )

    def test_attach_product_success(self):
        """Attach product to two BEs with DIFFERENT marketplaces — both succeed."""
        r1 = self.service.attach_product(
            product_id=str(self.product.id),
            business_entity_id=str(self.be_shopee_a.id),
            company_id=str(self.company.id),
        )
        self.assertTrue(r1["created"])

        r2 = self.service.attach_product(
            product_id=str(self.product.id),
            business_entity_id=str(self.be_tiktok.id),
            company_id=str(self.company.id),
        )
        self.assertTrue(r2["created"])

        count = self.ProductBusinessEntity.objects.filter(product=self.product).count()
        self.assertEqual(count, 2)

    def test_attach_product_same_marketplace_conflict(self):
        """Attach second BE with SAME marketplace — raises ValueError."""
        self.service.attach_product(
            product_id=str(self.product.id),
            business_entity_id=str(self.be_shopee_a.id),
            company_id=str(self.company.id),
        )

        with self.assertRaises(ValueError) as ctx:
            self.service.attach_product(
                product_id=str(self.product.id),
                business_entity_id=str(self.be_shopee_b.id),
                company_id=str(self.company.id),
            )
        self.assertIn("same marketplace", str(ctx.exception))

    def test_attach_product_different_marketplace_allowed(self):
        """Attach BE_A (shopee) then BE_B (tiktok) — both succeed, no conflict."""
        r1 = self.service.attach_product(
            product_id=str(self.product.id),
            business_entity_id=str(self.be_shopee_a.id),
            company_id=str(self.company.id),
        )
        self.assertTrue(r1["created"])

        r2 = self.service.attach_product(
            product_id=str(self.product.id),
            business_entity_id=str(self.be_tiktok.id),
            company_id=str(self.company.id),
        )
        self.assertTrue(r2["created"])

    def test_attach_product_idempotent(self):
        """Attach same (product, business_entity) pair twice — idempotent."""
        r1 = self.service.attach_product(
            product_id=str(self.product.id),
            business_entity_id=str(self.be_shopee_a.id),
            company_id=str(self.company.id),
        )
        self.assertTrue(r1["created"])

        r2 = self.service.attach_product(
            product_id=str(self.product.id),
            business_entity_id=str(self.be_shopee_a.id),
            company_id=str(self.company.id),
        )
        self.assertFalse(r2["created"])

        count = self.ProductBusinessEntity.objects.filter(
            product=self.product, business_entity=self.be_shopee_a
        ).count()
        self.assertEqual(count, 1)

    def test_detach_product_success(self):
        """Detach an existing assignment — row is deleted."""
        from apps.inventory.factories import ProductBusinessEntityFactory

        assignment = ProductBusinessEntityFactory(
            product=self.product,
            business_entity=self.be_shopee_a,
            company=self.company,
        )
        self.service.detach_product(
            product_business_entity_id=str(assignment.id),
            company_id=str(self.company.id),
        )
        self.assertFalse(self.ProductBusinessEntity.objects.filter(id=assignment.id).exists())

    def test_detach_product_wrong_company(self):
        """Detach with wrong company — raises ValueError."""
        from apps.inventory.factories import ProductBusinessEntityFactory

        other_company = CompanyFactory()
        assignment = ProductBusinessEntityFactory(
            product=self.product,
            business_entity=self.be_shopee_a,
            company=self.company,
        )
        with self.assertRaises(ValueError):
            self.service.detach_product(
                product_business_entity_id=str(assignment.id),
                company_id=str(other_company.id),
            )

    def test_attach_wrong_company_product(self):
        """Product belongs to different company — raises Product.DoesNotExist."""
        other_company = CompanyFactory()
        with self.assertRaises(Product.DoesNotExist):
            self.service.attach_product(
                product_id=str(self.product.id),
                business_entity_id=str(self.be_shopee_a.id),
                company_id=str(other_company.id),
            )


class TestCompanyMarketplaceService(TestCase):
    """Tests for BusinessEntityService.get_or_seed_company_marketplaces."""

    def setUp(self):
        from apps.inventory.services.business_entity_service import BusinessEntityService

        self.service = BusinessEntityService()
        self.company = CompanyFactory()

    def test_get_or_seed_creates_defaults(self):
        """Company with no CompanyMarketplace records seeds Shopee+TikTok."""
        result = self.service.get_or_seed_company_marketplaces(str(self.company.id))
        names = sorted(m.name for m in result)
        self.assertEqual(names, ["Shopee", "TikTok"])
        self.assertEqual(len(result), 2)

    def test_get_or_seed_idempotent(self):
        """Company already has records — calling again returns same, no duplicates."""
        from apps.inventory.factories import CompanyMarketplaceFactory

        CompanyMarketplaceFactory(company=self.company, name="Shopee")
        CompanyMarketplaceFactory(company=self.company, name="TikTok")
        result = self.service.get_or_seed_company_marketplaces(str(self.company.id))
        self.assertEqual(len(result), 2)

    def test_get_or_seed_returns_existing_custom(self):
        """Company has Shopee, TikTok, Lazada — returns all 3, no new ones."""
        from apps.inventory.factories import CompanyMarketplaceFactory

        CompanyMarketplaceFactory(company=self.company, name="Shopee")
        CompanyMarketplaceFactory(company=self.company, name="TikTok")
        CompanyMarketplaceFactory(company=self.company, name="Lazada")
        result = self.service.get_or_seed_company_marketplaces(str(self.company.id))
        self.assertEqual(len(result), 3)
        names = sorted(m.name for m in result)
        self.assertEqual(names, ["Lazada", "Shopee", "TikTok"])


class TestCompanyMarketplaceAPI(APITestCase):
    """Tests for CompanyMarketplace API endpoints."""

    def setUp(self):
        self.company = CompanyFactory()
        self.other_company = CompanyFactory()
        self.user = User.objects.create_user(
            username="cm_test_user", password="password", is_staff=True
        )
        from core.models import UserProfile

        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_list_auto_seeds(self):
        """GET /company-marketplaces/ — auto-seeds Shopee+TikTok for new company."""
        response = self.client.get("/company-marketplaces/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        names = sorted(m["name"] for m in response.data["results"])
        self.assertEqual(names, ["Shopee", "TikTok"])

    def test_list_company_isolation(self):
        """Company A only sees their own marketplaces."""
        from apps.inventory.factories import CompanyMarketplaceFactory

        CompanyMarketplaceFactory(company=self.company, name="Shopee")
        CompanyMarketplaceFactory(company=self.other_company, name="TikTok")
        response = self.client.get("/company-marketplaces/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [m["name"] for m in response.data["results"]]
        self.assertIn("Shopee", names)
        self.assertNotIn("TikTok", names)

    def test_create_custom(self):
        """POST /company-marketplaces/ — creates a custom marketplace."""
        response = self.client.post(
            "/company-marketplaces/",
            {"name": "Lazada"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], "Lazada")

    def test_create_duplicate_name_fails(self):
        """POST with duplicate name — 400."""
        from apps.inventory.factories import CompanyMarketplaceFactory

        CompanyMarketplaceFactory(company=self.company, name="Lazada")
        response = self.client.post(
            "/company-marketplaces/",
            {"name": "Lazada"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update(self):
        """PATCH /company-marketplaces/{id}/ — updates is_active."""
        from apps.inventory.factories import CompanyMarketplaceFactory

        cm = CompanyMarketplaceFactory(company=self.company, name="Shopee")
        response = self.client.patch(
            f"/company-marketplaces/{cm.id}/",
            {"is_active": False},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["is_active"])

    def test_delete_success(self):
        """DELETE /company-marketplaces/{id}/ — 204 when no BEs reference it."""
        from apps.inventory.factories import CompanyMarketplaceFactory

        cm = CompanyMarketplaceFactory(company=self.company, name="Shopee")
        response = self.client.delete(f"/company-marketplaces/{cm.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_delete_blocked_by_business_entity(self):
        """DELETE when BusinessEntity references it — 409."""
        from apps.inventory.factories import BusinessEntityFactory, CompanyMarketplaceFactory

        cm = CompanyMarketplaceFactory(company=self.company, name="Shopee")
        BusinessEntityFactory(company=self.company, marketplace=cm)
        response = self.client.delete(f"/company-marketplaces/{cm.id}/")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)


class TestBusinessEntityAPI(APITestCase):
    """Tests for BusinessEntity and ProductBusinessEntity API endpoints."""

    def setUp(self):
        from apps.inventory.factories import CompanyMarketplaceFactory

        self.company = CompanyFactory()
        self.other_company = CompanyFactory()
        self.user = User.objects.create_user(
            username="be_test_user", password="password", is_staff=True
        )
        from core.models import UserProfile

        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

        self.marketplace_shopee = CompanyMarketplaceFactory(company=self.company, name="Shopee")
        self.marketplace_tiktok = CompanyMarketplaceFactory(company=self.company, name="TikTok")
        self.inactive_marketplace = CompanyMarketplaceFactory(
            company=self.company, name="Inactive", is_active=False
        )

    def test_list_business_entities(self):
        """GET /business-entities/ returns only the company's BEs."""
        from apps.inventory.factories import BusinessEntityFactory

        BusinessEntityFactory(company=self.company, marketplace=self.marketplace_shopee)
        BusinessEntityFactory(company=self.company, marketplace=self.marketplace_tiktok)
        BusinessEntityFactory(company=self.other_company, marketplace=self.marketplace_shopee)

        response = self.client.get("/business-entities/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 2)

    def test_create_business_entity(self):
        """POST /business-entities/ with name + marketplace_id — 201."""
        response = self.client.post(
            "/business-entities/",
            {"name": "CV A", "marketplace_id": str(self.marketplace_shopee.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["marketplace_name"], "Shopee")

    def test_create_duplicate_name_same_company_fails(self):
        """POST with duplicate name for same company — 400."""
        self.client.post(
            "/business-entities/",
            {"name": "CV A", "marketplace_id": str(self.marketplace_shopee.id)},
            format="json",
        )
        response = self.client.post(
            "/business-entities/",
            {"name": "CV A", "marketplace_id": str(self.marketplace_tiktok.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_business_entity(self):
        """PATCH /business-entities/{id}/ — updates name."""
        from apps.inventory.factories import BusinessEntityFactory

        be = BusinessEntityFactory(company=self.company, marketplace=self.marketplace_shopee)
        response = self.client.patch(
            f"/business-entities/{be.id}/",
            {"name": "Updated Name"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Updated Name")

    def test_list_product_business_entities_filtered(self):
        """GET /product-business-entities/?product_id=X — filtered."""
        from apps.inventory.factories import (
            BusinessEntityFactory,
            ProductBusinessEntityFactory,
            ProductFactory,
        )

        product_a = ProductFactory(company=self.company, category__company=self.company)
        product_b = ProductFactory(company=self.company, category__company=self.company)
        be = BusinessEntityFactory(company=self.company, marketplace=self.marketplace_shopee)

        ProductBusinessEntityFactory(product=product_a, business_entity=be, company=self.company)
        ProductBusinessEntityFactory(product=product_b, business_entity=be, company=self.company)

        response = self.client.get(f"/product-business-entities/?product_id={product_a.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(len(response.data["results"]), 1)

    def test_attach_success(self):
        """POST /product-business-entities/ — 201."""
        from apps.inventory.factories import BusinessEntityFactory, ProductFactory

        product = ProductFactory(company=self.company, category__company=self.company)
        be = BusinessEntityFactory(company=self.company, marketplace=self.marketplace_shopee)

        response = self.client.post(
            "/product-business-entities/",
            {"product_id": str(product.id), "business_entity_id": str(be.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["created"])

    def test_attach_conflict_returns_400(self):
        """Attach product to two BEs with same marketplace — 400."""
        from apps.inventory.factories import BusinessEntityFactory, ProductFactory

        product = ProductFactory(company=self.company, category__company=self.company)
        be_a = BusinessEntityFactory(company=self.company, marketplace=self.marketplace_shopee)
        be_b = BusinessEntityFactory(company=self.company, marketplace=self.marketplace_shopee)

        self.client.post(
            "/product-business-entities/",
            {"product_id": str(product.id), "business_entity_id": str(be_a.id)},
            format="json",
        )
        response = self.client.post(
            "/product-business-entities/",
            {"product_id": str(product.id), "business_entity_id": str(be_b.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("same marketplace", str(response.data["error"]))

    def test_detach_success(self):
        """DELETE /product-business-entities/{id}/ — 204."""
        from apps.inventory.factories import (
            BusinessEntityFactory,
            ProductBusinessEntityFactory,
            ProductFactory,
        )

        product = ProductFactory(company=self.company, category__company=self.company)
        be = BusinessEntityFactory(company=self.company, marketplace=self.marketplace_shopee)
        assignment = ProductBusinessEntityFactory(
            product=product, business_entity=be, company=self.company
        )

        response = self.client.delete(f"/product-business-entities/{assignment.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_detach_not_found(self):
        """DELETE /product-business-entities/{unknown}/ — 404."""
        response = self.client.delete(
            "/product-business-entities/00000000-0000-0000-0000-000000000000/"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


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


class AdjustStockBatchServiceTest(TestCase):
    """Tests for InventoryService.adjust_stock_batch"""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.product_variant = ProductVariantFactory(company=self.company)
        self.pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=10,
        )
        self.service = InventoryService()

    def test_add_increases_stock(self):
        result = self.service.adjust_stock_batch(
            [
                {
                    "variant_id": str(self.product_variant.id),
                    "warehouse_id": str(self.warehouse.id),
                    "type": "add",
                    "qty": 5,
                }
            ]
        )
        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 15)
        self.assertEqual(result["results"][0]["old_qty"], 10)
        self.assertEqual(result["results"][0]["new_qty"], 15)

    def test_min_decreases_stock(self):
        result = self.service.adjust_stock_batch(
            [
                {
                    "variant_id": str(self.product_variant.id),
                    "warehouse_id": str(self.warehouse.id),
                    "type": "min",
                    "qty": 3,
                }
            ]
        )
        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 7)
        self.assertEqual(result["results"][0]["old_qty"], 10)
        self.assertEqual(result["results"][0]["new_qty"], 7)

    def test_set_sets_absolute_qty(self):
        result = self.service.adjust_stock_batch(
            [
                {
                    "variant_id": str(self.product_variant.id),
                    "warehouse_id": str(self.warehouse.id),
                    "type": "set",
                    "qty": 20,
                }
            ]
        )
        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 20)
        self.assertEqual(result["results"][0]["old_qty"], 10)
        self.assertEqual(result["results"][0]["new_qty"], 20)

    def test_set_to_zero(self):
        result = self.service.adjust_stock_batch(
            [
                {
                    "variant_id": str(self.product_variant.id),
                    "warehouse_id": str(self.warehouse.id),
                    "type": "set",
                    "qty": 0,
                }
            ]
        )
        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 0)
        self.assertEqual(result["results"][0]["new_qty"], 0)

    def test_min_insufficient_stock_returns_error(self):
        result = self.service.adjust_stock_batch(
            [
                {
                    "variant_id": str(self.product_variant.id),
                    "warehouse_id": str(self.warehouse.id),
                    "type": "min",
                    "qty": 15,
                }
            ]
        )
        self.assertEqual(len(result["errors"]), 1)
        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 10)

    def test_set_negative_rejected(self):
        result = self.service.adjust_stock_batch(
            [
                {
                    "variant_id": str(self.product_variant.id),
                    "warehouse_id": str(self.warehouse.id),
                    "type": "set",
                    "qty": -1,
                }
            ]
        )
        self.assertEqual(len(result["errors"]), 1)

    def test_warehouse_ulid_id_no_crash(self):
        result = self.service.adjust_stock_batch(
            [
                {
                    "variant_id": str(self.product_variant.id),
                    "warehouse_id": str(self.warehouse.id),
                    "type": "add",
                    "qty": 1,
                }
            ]
        )
        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 11)
        self.assertEqual(len(result["results"]), 1)

    def test_stock_movement_created_on_add(self):
        self.service.adjust_stock_batch(
            [
                {
                    "variant_id": str(self.product_variant.id),
                    "warehouse_id": str(self.warehouse.id),
                    "type": "add",
                    "qty": 5,
                }
            ]
        )
        movement = StockMovement.objects.last()
        self.assertIsNotNone(movement)
        self.assertEqual(movement.movement_type, StockMovement.MovementType.ADJUSTMENT)
        self.assertEqual(movement.quantity, 5)

    def test_stock_movement_created_on_min(self):
        self.service.adjust_stock_batch(
            [
                {
                    "variant_id": str(self.product_variant.id),
                    "warehouse_id": str(self.warehouse.id),
                    "type": "min",
                    "qty": 3,
                }
            ]
        )
        movement = StockMovement.objects.last()
        self.assertIsNotNone(movement)
        self.assertEqual(movement.movement_type, StockMovement.MovementType.ADJUSTMENT)
        self.assertEqual(movement.quantity, -3)


class QCPPhase1Test(APITestCase):
    """Tests for QCP Phase 1 — Variant Photo, Supplier Link, Default Variant."""

    def setUp(self):
        self.company = CompanyFactory()
        self.category = CategoryFactory(company=self.company)
        self.user = User.objects.create_user(
            username="qcp1_user", password="password", is_staff=True
        )
        from core.models import UserProfile

        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_product_variant_photo_field_nullable(self):
        """Variant photo is nullable (None by default)."""
        variant = ProductVariantFactory(company=self.company)
        self.assertFalse(variant.photo)

    def test_variant_serializer_photo_url_none_when_no_photo(self):
        """VariantSerializer returns None photo_url when no photo."""
        from apps.inventory.serializers import VariantSerializer

        variant = ProductVariantFactory(company=self.company)
        serializer = VariantSerializer(variant)
        self.assertIsNone(serializer.data["photo_url"])

    def test_product_photo_url_falls_back_to_product_photo(self):
        """ProductVariantStockSerializer falls back to product photo when variant has none."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.inventory.serializers import ProductVariantStockSerializer

        product = ProductFactory(company=self.company, category=self.category)
        variant = ProductVariantFactory(product=product, company=self.company)
        product.product_photo = SimpleUploadedFile("test.jpg", b"x")
        product.save()
        serializer = ProductVariantStockSerializer(variant)
        photo_url = serializer.data["product_photo_url"]
        self.assertIsNotNone(photo_url)
        self.assertIn("test.jpg", photo_url)

    def test_product_photo_url_uses_variant_photo_when_set(self):
        """ProductVariantStockSerializer uses variant photo when set."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.inventory.serializers import ProductVariantStockSerializer

        product = ProductFactory(company=self.company, category=self.category)
        variant = ProductVariantFactory(product=product, company=self.company)
        variant.photo = SimpleUploadedFile("variant.jpg", b"x")
        variant.save()
        serializer = ProductVariantStockSerializer(variant)
        photo_url = serializer.data["product_photo_url"]
        self.assertIsNotNone(photo_url)
        self.assertIn("variant", photo_url)

    def test_product_supplier_link_returns_none_when_no_supplier(self):
        """product_supplier_link is None when no ProductSupplier exists."""
        from apps.inventory.serializers import ProductVariantStockSerializer

        product = ProductFactory(company=self.company, category=self.category)
        variant = ProductVariantFactory(product=product, company=self.company)
        serializer = ProductVariantStockSerializer(variant)
        self.assertIsNone(serializer.data["product_supplier_link"])

    def test_product_supplier_link_returns_first_supplier_link(self):
        """product_supplier_link returns first ProductSupplier link without supplier_id filter."""
        from apps.inventory.serializers import ProductVariantStockSerializer

        product = ProductFactory(company=self.company, category=self.category)
        variant = ProductVariantFactory(product=product, company=self.company)
        ProductSupplierFactory(
            product=product, company=self.company, supplier_link="https://example.com"
        )
        serializer = ProductVariantStockSerializer(variant)
        self.assertEqual(serializer.data["product_supplier_link"], "https://example.com")

    def test_product_supplier_link_filtered_by_supplier_id(self):
        """product_supplier_link respects supplier_id query param."""
        from apps.inventory.serializers import ProductVariantStockSerializer

        product = ProductFactory(company=self.company, category=self.category)
        variant = ProductVariantFactory(product=product, company=self.company)
        supplier1 = SupplierFactory(company=self.company)
        supplier2 = SupplierFactory(company=self.company)
        ProductSupplierFactory(
            product=product,
            supplier=supplier1,
            company=self.company,
            supplier_link="https://link1.com",
        )
        ProductSupplierFactory(
            product=product,
            supplier=supplier2,
            company=self.company,
            supplier_link="https://link2.com",
        )
        request = self.client.get("/").wsgi_request
        request.query_params = {"supplier_id": str(supplier2.id)}  # type: ignore[attr-defined]
        serializer = ProductVariantStockSerializer(variant, context={"request": request})
        self.assertEqual(serializer.data["product_supplier_link"], "https://link2.com")

    def test_create_product_auto_creates_default_variant(self):
        """POST /product/ with variants=[] creates a Default variant."""
        payload = {
            "company_id": str(self.company.id),
            "category_id": str(self.category.id),
            "name": "Default Variant Product",
            "description": "A" * 25,
            "variant_options": {},
            "variants": [],
        }
        response = self.client.post("/product/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(response.data["variants"]), 1)
        self.assertEqual(response.data["variants"][0]["name"], "Default")
        self.assertTrue(response.data["variants"][0]["sku_variant_code"].endswith("-DEFAULT"))

    def test_create_product_with_variants_does_not_create_default(self):
        """POST /product/ with explicit variants does not create a Default variant."""
        payload = {
            "company_id": str(self.company.id),
            "category_id": str(self.category.id),
            "name": "Normal Product",
            "description": "A" * 25,
            "variants": [
                {
                    "variant_values": {"size": "L"},
                    "base_price": 100000,
                }
            ],
        }
        response = self.client.post("/product/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(response.data["variants"]), 1)
        self.assertNotEqual(response.data["variants"][0]["name"], "Default")
        self.assertFalse(response.data["variants"][0]["sku_variant_code"].endswith("-DEFAULT"))

    def test_create_product_with_supplier_creates_product_supplier(self):
        """POST /product/ with supplier_id and supplier_link creates ProductSupplier."""
        supplier = SupplierFactory(company=self.company)
        payload = {
            "company_id": str(self.company.id),
            "category_id": str(self.category.id),
            "name": "Supplier Linked Product",
            "description": "A" * 25,
            "variants": [
                {
                    "variant_values": {"color": "Red"},
                    "base_price": 50000,
                }
            ],
            "supplier_id": str(supplier.id),
            "supplier_link": "https://1688.com/product/123",
        }
        response = self.client.post("/product/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        product_id = response.data["id"]
        self.assertEqual(ProductSupplier.objects.filter(product_id=product_id).count(), 1)
        ps = ProductSupplier.objects.filter(product_id=product_id).first()
        self.assertEqual(ps.supplier_link, "https://1688.com/product/123")
        self.assertEqual(ps.supplier_id, supplier.id)

    def test_create_product_supplier_id_optional(self):
        """POST /product/ without supplier_id does not create ProductSupplier."""
        payload = {
            "company_id": str(self.company.id),
            "category_id": str(self.category.id),
            "name": "No Supplier Product",
            "description": "A" * 25,
            "variants": [
                {
                    "variant_values": {"color": "Blue"},
                    "base_price": 60000,
                }
            ],
        }
        response = self.client.post("/product/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(ProductSupplier.objects.count(), 0)

    def test_upload_variant_photo(self):
        """POST /product/<pid>/variants/<vid>/photo/ uploads photo."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        product = ProductFactory(company=self.company, category=self.category)
        variant = ProductVariantFactory(product=product, company=self.company)
        image = SimpleUploadedFile("photo.jpg", b"image_data", content_type="image/jpeg")
        response = self.client.post(
            f"/product/{product.id}/variants/{variant.id}/photo/",
            {"image": image},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.data["photo_url"])
        variant.refresh_from_db()
        self.assertTrue(variant.photo)

    def test_delete_variant_photo(self):
        """DELETE /product/<pid>/variants/<vid>/photo/ deletes photo."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        product = ProductFactory(company=self.company, category=self.category)
        variant = ProductVariantFactory(product=product, company=self.company)
        variant.photo = SimpleUploadedFile("photo.jpg", b"x")
        variant.save()
        response = self.client.delete(
            f"/product/{product.id}/variants/{variant.id}/photo/",
        )
        self.assertEqual(response.status_code, 204)
        variant.refresh_from_db()
        self.assertFalse(variant.photo)

    def test_upload_variant_photo_wrong_product(self):
        """Uploading photo to variant that belongs to a different product returns 404."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        product = ProductFactory(company=self.company, category=self.category)
        other_product = ProductFactory(company=self.company, category=self.category)
        variant = ProductVariantFactory(product=other_product, company=self.company)
        image = SimpleUploadedFile("photo.jpg", b"x", content_type="image/jpeg")
        response = self.client.post(
            f"/product/{product.id}/variants/{variant.id}/photo/",
            {"image": image},
            format="multipart",
        )
        self.assertEqual(response.status_code, 404)


class QCPPhase5Test(APITestCase):
    """Tests for QCP Phase 5 — product_photo_url from gallery photos."""

    def setUp(self):
        from core.models import UserProfile

        self.company = CompanyFactory()
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.user = User.objects.create_user(
            username="qcp5_inv_user", password="password", is_staff=True
        )
        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_variant_stock_photo_url_from_gallery(self):
        """Gallery photo is returned when variant.photo is null."""
        variant = ProductVariantFactory(product=self.product, company=self.company)
        ProductPhotoFactory(product=self.product, company=self.company, order=0)
        response = self.client.get("/product-variants/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result = next(r for r in response.data["results"] if r["id"] == str(variant.id))
        photo_url = result["product_photo_url"]
        self.assertIsNotNone(photo_url)
        self.assertIn("test_photo", photo_url)

    def test_variant_stock_photo_url_variant_takes_priority(self):
        """Variant photo takes priority over gallery photo."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        variant = ProductVariantFactory(product=self.product, company=self.company)
        variant.photo = SimpleUploadedFile("variant.jpg", b"x")
        variant.save()
        ProductPhotoFactory(product=self.product, company=self.company, order=0)
        response = self.client.get("/product-variants/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result = next(r for r in response.data["results"] if r["id"] == str(variant.id))
        photo_url = result["product_photo_url"]
        self.assertIsNotNone(photo_url)
        self.assertIn("variant", photo_url)


class QCPPhase6Test(APITestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.user = User.objects.create_user(username="qcp6", password="pass")
        from core.models import UserProfile

        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)
        category = CategoryFactory(company=self.company)
        self.product = ProductFactory(company=self.company, category=category)

    def test_photo_proxy_returns_image_bytes(self):
        """photo-proxy returns image bytes when gallery photo exists."""
        ProductPhotoFactory(product=self.product, company=self.company, order=0)
        response = self.client.get(f"/product/{self.product.id}/photo-proxy/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("image", response.get("Content-Type", ""))

    def test_photo_proxy_returns_404_when_no_photo(self):
        """photo-proxy returns 404 when product has no photos."""
        response = self.client.get(f"/product/{self.product.id}/photo-proxy/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_photo_proxy_requires_auth(self):
        """photo-proxy returns 404 when unauthenticated (empty queryset)."""
        self.client.force_authenticate(user=None)
        response = self.client.get(f"/product/{self.product.id}/photo-proxy/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


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

    def test_variant_stock_serializer_includes_last_price(self):
        self.product_variant.last_unit_price_foreign = Decimal("18.50")
        self.product_variant.last_currency = "CNY"
        self.product_variant.save()
        response = self.client.get("/product-variants/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result = next(
            r for r in response.data["results"] if r["id"] == str(self.product_variant.id)
        )
        self.assertEqual(result["last_unit_price_foreign"], "18.5000")
        self.assertEqual(result["last_currency"], "CNY")

    def test_po_detail_serializer_includes_variant_values(self):
        from apps.purchasing.factories import PurchaseOrderDetailFactory, PurchaseOrderFactory

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
        from apps.inventory.factories import ProductSupplierFactory, SupplierFactory
        from apps.purchasing.factories import PurchaseOrderFactory

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
        from apps.inventory.factories import ProductSupplierFactory, SupplierFactory
        from apps.purchasing.factories import PurchaseOrderFactory

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


class DimensionImageAPITest(APITestCase):
    def setUp(self):
        from core.models import UserProfile

        self.company = CompanyFactory()
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(
            category=self.category, company=self.company, dim1_key="Warna"
        )
        self.user = User.objects.create_user(
            username="dim_api_user", password="password", is_staff=True
        )
        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_product_dim_fields_default_to_empty_string_and_list(self):
        product = ProductFactory(company=self.company, category=self.category)
        self.assertEqual(product.dim1_key, "")
        self.assertEqual(product.dim2_key, "")
        self.assertEqual(product.dim1_options, [])
        self.assertEqual(product.dim2_options, [])

    def test_dimension_image_upload_creates_record(self):
        photo = SimpleUploadedFile("w.jpg", b"imgdata", content_type="image/jpeg")
        url = f"/product/{self.product.id}/dimension-image/"
        response = self.client.post(
            url,
            {"dim_key": "Warna", "dim_value": "White", "photo": photo},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            ProductDimensionImage.objects.filter(
                product=self.product, dim_key="Warna", dim_value="White"
            ).exists()
        )
        self.assertIn("photo_url", response.data)
        created = ProductDimensionImage.objects.get(product=self.product, dim_key="Warna", dim_value="White")
        self.assertEqual(created.company, self.product.company)

    def test_dimension_image_upload_replaces_existing_for_same_key_and_value(self):
        existing = ProductDimensionImageFactory(
            product=self.product, dim_key="Warna", dim_value="White"
        )
        original_pk = existing.pk
        photo = SimpleUploadedFile("w2.jpg", b"newdata", content_type="image/jpeg")
        url = f"/product/{self.product.id}/dimension-image/"
        response = self.client.post(
            url,
            {"dim_key": "Warna", "dim_value": "White", "photo": photo},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            ProductDimensionImage.objects.filter(
                product=self.product, dim_key="Warna", dim_value="White"
            ).count(),
            1,
        )
        updated = ProductDimensionImage.objects.get(
            product=self.product, dim_key="Warna", dim_value="White"
        )
        self.assertEqual(updated.pk, original_pk)

    def test_dimension_image_delete_removes_record(self):
        ProductDimensionImageFactory(
            product=self.product, dim_key="Warna", dim_value="White"
        )
        url = f"/product/{self.product.id}/dimension-image/"
        response = self.client.delete(
            url, {"dim_key": "Warna", "dim_value": "White"}, format="json"
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            ProductDimensionImage.objects.filter(
                product=self.product, dim_key="Warna", dim_value="White"
            ).exists()
        )

    def test_dimension_image_delete_missing_returns_404(self):
        url = f"/product/{self.product.id}/dimension-image/"
        response = self.client.delete(
            url, {"dim_key": "Warna", "dim_value": "NonExistent"}, format="json"
        )
        self.assertEqual(response.status_code, 404)

    def test_photo_proxy_returns_dimension_image_when_dim_key_and_value_provided(self):
        ProductDimensionImageFactory(
            product=self.product, dim_key="Warna", dim_value="White"
        )
        url = f"/product/{self.product.id}/photo-proxy/?dim_key=Warna&dim_value=White"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_photo_proxy_falls_back_to_gallery_when_no_dimension_image_matches(self):
        from apps.inventory.factories import ProductPhotoFactory
        ProductPhotoFactory(product=self.product, company=self.company, order=0)
        url = f"/product/{self.product.id}/photo-proxy/?dim_key=Warna&dim_value=NonExistent"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_dimension_image_upload_missing_dim_key_returns_400(self):
        photo = SimpleUploadedFile("w.jpg", b"imgdata", content_type="image/jpeg")
        url = f"/product/{self.product.id}/dimension-image/"
        response = self.client.post(
            url, {"dim_value": "White", "photo": photo}, format="multipart"
        )
        self.assertEqual(response.status_code, 400)

    def test_dimension_image_upload_missing_dim_value_returns_400(self):
        photo = SimpleUploadedFile("w.jpg", b"imgdata", content_type="image/jpeg")
        url = f"/product/{self.product.id}/dimension-image/"
        response = self.client.post(
            url, {"dim_key": "Warna", "photo": photo}, format="multipart"
        )
        self.assertEqual(response.status_code, 400)

    def test_dimension_image_upload_missing_photo_returns_400(self):
        url = f"/product/{self.product.id}/dimension-image/"
        response = self.client.post(
            url, {"dim_key": "Warna", "dim_value": "White"}, format="multipart"
        )
        self.assertEqual(response.status_code, 400)

    def test_product_detail_response_includes_dim_fields_and_dimension_images(self):
        response = self.client.get(f"/product/{self.product.id}/")
        self.assertEqual(response.status_code, 200)
        for field in ("dim1_key", "dim2_key", "dim1_options", "dim2_options", "dimension_images"):
            self.assertIn(field, response.data)
        self.assertIsInstance(response.data["dimension_images"], list)

    def test_dimension_image_upload_rejected_for_product_from_other_company(self):
        """A user from company A cannot upload a dimension image on a product from company B."""
        other_company = CompanyFactory()
        other_category = CategoryFactory(company=other_company)
        other_product = ProductFactory(company=other_company, category=other_category)
        photo = SimpleUploadedFile("w.jpg", b"imgdata", content_type="image/jpeg")
        url = f"/product/{other_product.id}/dimension-image/"
        response = self.client.post(
            url,
            {"dim_key": "Warna", "dim_value": "White", "photo": photo},
            format="multipart",
        )
        # get_queryset filters by company, so this returns 404 (not 403)
        self.assertEqual(response.status_code, 404)


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
        from apps.inventory.factories import ProductDimensionImageFactory
        from apps.purchasing.factories import PurchaseOrderDetailFactory, PurchaseOrderFactory

        po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        PurchaseOrderDetailFactory(
            purchase_order=po, product_variant=self.variant, company=self.company
        )
        ProductDimensionImageFactory(
            product=self.product, dim_key="Warna", dim_value="White"
        )

        response = self.client.get(f"/purchase-order/{po.id}/", format="json")
        self.assertEqual(response.status_code, 200)
        detail_data = response.data["order_details"][0]
        self.assertIn("product_photo_url", detail_data)
        self.assertIsNotNone(detail_data["product_photo_url"])
        self.assertIn("dimension_images", detail_data["product_photo_url"])

    def test_po_detail_falls_back_to_variant_photo_when_no_dimension_image(self):
        from django.core.files.base import ContentFile

        from apps.purchasing.factories import PurchaseOrderDetailFactory, PurchaseOrderFactory

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
        from apps.purchasing.factories import PurchaseOrderDetailFactory, PurchaseOrderFactory

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
        from apps.purchasing.factories import PurchaseOrderDetailFactory, PurchaseOrderFactory

        product_no_dim = ProductFactory(
            category=self.category, company=self.company, dim1_key=""
        )
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
