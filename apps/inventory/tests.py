# apps/inventory/tests/test_api.py
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
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
    ProductFactory,
    ProductSupplierFactory,
    ProductVariantFactory,
    ProductVariantWarehouseFactory,
    StockMovementFactory,
    SupplierFactory,
)
from apps.inventory.models import (
    Product,
    ProductCogs,
    ProductPhoto,
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
                        "base_price": 180000,
                    },
                    {
                        "variant_values": {"warna": "Navy", "size": "XL"},
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
                        "base_price": 180000,
                    },
                    {
                        "variant_values": {"warna": "Blue", "size": "XL"},
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
            self.assertIn("BLUE", v.sku_variant_code)  # Assuming your trigger uppercases it

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
                        "base_price": 180000,
                    },
                    {
                        "variant_values": {"warna": "Blue", "size": "XL"},
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
                    "sku_variant_code": "TSH-001-BLU-M",
                    "base_price": 50000,
                }
            ],
        }
        response = self.client.post("/product/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertIn("id", response.data)
        self.assertEqual(len(response.data["variants"]), 1)
        self.assertEqual(response.data["variants"][0]["sku_variant_code"], "TSH-001-BLU-M")
        self.assertEqual(response.data["variants"][0]["name"], "Blue")

    def test_create_product_sets_sku_variant_code_in_db(self):
        """sku_variant_code from request is saved to the ProductVariant record."""
        payload = {
            "company_id": str(self.company.id),
            "category_id": str(self.category.id),
            "name": "SKU Test Product",
            "description": "SKU Test Product (created via PO - update description later)",
            "variants": [
                {
                    "variant_values": {"color": "red"},
                    "sku_variant_code": "SKU-001-RED-L",
                    "base_price": 75000,
                }
            ],
        }
        response = self.client.post("/product/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        variant_id = response.data["variants"][0]["id"]
        variant = ProductVariant.objects.get(id=variant_id)
        self.assertEqual(variant.sku_variant_code, "SKU-001-RED-L")


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
            company=self.company_a, category=self.category_a,
            is_active=False, description="A" * 25
        )
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.get("/product/")
        self.assertEqual(resp.status_code, 200)
        ids = [p["id"] for p in resp.data["results"]]
        self.assertIn(str(inactive.id), ids)

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
        from django.core.files.uploadedfile import SimpleUploadedFile

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
        ProductPhoto.objects.create(
            product=product,
            company=self.company,
            image=SimpleUploadedFile("primary_test.jpg", b"x"),
            is_primary=True,
            order=0,
        )

        response = self.client.get("/inventory-summary/")
        self.assertEqual(response.status_code, 200)
        photo_url = response.data["products"][0]["photo_url"]
        self.assertIsNotNone(photo_url)
        self.assertIn("primary_test.jpg", photo_url)

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


class TestProductVariantSupplierCRUD(APITestCase):
    """Tests for ProductVariantSupplier CRUD endpoints"""

    def setUp(self):
        from apps.inventory.factories import SupplierFactory
        self.company = CompanyFactory()
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.variant = ProductVariantFactory(product=self.product, company=self.company)
        self.supplier = SupplierFactory(company=self.company)
        self.user = User.objects.create_user(
            username="pvs_test_user", password="password", is_staff=True
        )
        from core.models import UserProfile
        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_link_variant_to_supplier(self):
        response = self.client.post(
            "/variant-suppliers/",
            {
                "product_variant_id": str(self.variant.id),
                "supplier_id": str(self.supplier.id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["supplier_name"], self.supplier.name)

    def test_set_primary_supplier(self):
        s1 = self.client.post(
            "/variant-suppliers/",
            {
                "product_variant_id": str(self.variant.id),
                "supplier_id": str(self.supplier.id),
                "is_primary": True,
            },
            format="json",
        ).data
        self.assertTrue(s1["is_primary"])
        supplier2 = SupplierFactory(company=self.company)
        s2 = self.client.post(
            "/variant-suppliers/",
            {
                "product_variant_id": str(self.variant.id),
                "supplier_id": str(supplier2.id),
                "is_primary": True,
            },
            format="json",
        ).data
        self.assertTrue(s2["is_primary"])
        s1_resp = self.client.get(f"/variant-suppliers/{s1['id']}/", format="json")
        self.assertFalse(s1_resp.data["is_primary"])

    def test_list_by_variant(self):
        self.client.post(
            "/variant-suppliers/",
            {
                "product_variant_id": str(self.variant.id),
                "supplier_id": str(self.supplier.id),
            },
            format="json",
        )
        response = self.client.get(
            "/variant-suppliers/", {"product_variant_id": self.variant.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", response.data)
        self.assertEqual(len(results), 1)

    def test_list_by_supplier(self):
        self.client.post(
            "/variant-suppliers/",
            {
                "product_variant_id": str(self.variant.id),
                "supplier_id": str(self.supplier.id),
            },
            format="json",
        )
        response = self.client.get(
            "/variant-suppliers/", {"supplier_id": self.supplier.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", response.data)
        self.assertEqual(len(results), 1)

    def test_update_supplier_link(self):
        pvs = self.client.post(
            "/variant-suppliers/",
            {
                "product_variant_id": str(self.variant.id),
                "supplier_id": str(self.supplier.id),
            },
            format="json",
        ).data
        response = self.client.patch(
            f"/variant-suppliers/{pvs['id']}/",
            {"supplier_link": "https://example.com/new-link"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["supplier_link"], "https://example.com/new-link")


class SaveVariantsTest(APITestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(company=self.company, category=self.category, description="A" * 25)
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
        self.assertEqual(ProductVariant.objects.filter(product=self.product, is_active=True).count(), 2)
        variants = ProductVariant.objects.filter(product=self.product, is_active=True).order_by("base_price")
        self.assertEqual(variants[0].name, "Red")
        self.assertEqual(variants[1].name, "Blue")

    def test_save_variants_updates_existing_variant(self):
        """POST save_variants with existing variant id → updates base_price"""
        variant = ProductVariantFactory(product=self.product, company=self.company, variant_values={"color": "Red"})
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
        v1 = ProductVariantFactory(product=self.product, company=self.company, variant_values={"color": "Red"})
        v2 = ProductVariantFactory(product=self.product, company=self.company, variant_values={"color": "Blue"})
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
        v1 = ProductVariantFactory(product=self.product, company=self.company, variant_values={"color": "Red"})
        v2 = ProductVariantFactory(
            product=self.product, company=self.company,
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
        ProductSupplierFactory(product=self.product_a, supplier=self.supplier_a, company=self.company)
        ProductSupplierFactory(product=self.product_b, supplier=self.supplier_b, company=self.company)

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
        self.product = ProductFactory(company=self.company, category=self.category, description="A" * 25)
        self.supplier = SupplierFactory(company=self.company)
        self.user = User.objects.create_user(username="ps_test", password="pw", is_staff=True)
        from core.models import UserProfile
        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_create_product_supplier(self):
        resp = self.client.post("/product-suppliers/", {
            "product_id": str(self.product.id),
            "supplier_id": str(self.supplier.id),
            "supplier_link": "https://supplier.com/product-x",
        }, format="json")
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
        ps = ProductSupplierFactory(product=self.product, supplier=self.supplier, company=self.company)
        resp = self.client.delete(f"/product-suppliers/{ps.id}/")
        self.assertEqual(resp.status_code, 204)

    def test_variant_filter_by_supplier_via_product(self):
        """GET /product-variants/?supplier_id=X returns variants whose product is linked to supplier"""
        variant = ProductVariantFactory(product=self.product, company=self.company)
        other_product = ProductFactory(company=self.company, category=self.category, description="B" * 25)
        other_variant = ProductVariantFactory(product=other_product, company=self.company)
        ProductSupplierFactory(product=self.product, supplier=self.supplier, company=self.company)
        resp = self.client.get("/product-variants/", {"supplier_id": str(self.supplier.id)})
        self.assertEqual(resp.status_code, 200)
        ids = [v["id"] for v in resp.data["results"]]
        self.assertIn(str(variant.id), ids)
        self.assertNotIn(str(other_variant.id), ids)
