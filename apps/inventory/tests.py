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
    ProductVariantFactory,
    ProductVariantWarehouseFactory,
    StockMovementFactory,
)
from apps.inventory.models import (
    Product,
    ProductCogs,
    ProductPhoto,
    ProductVariant,
    ProductVariantMarketplace,
    ProductVariantWarehouse,
    StockMovement,
)
from apps.inventory.services.inventory_service import InventoryService
from apps.purchasing.factories import PurchaseOrderFactory
from apps.purchasing.models import PurchaseOrder
from core.factories import CompanyFactory, MarketplaceFactory, WarehouseFactory
from core.permissions import IsStaffOrReadOnly as StaffPerm

_real_staff_perm = StaffPerm.has_permission


class InventoryAPITest(APITestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.category = CategoryFactory(company=self.company)
        self.marketplace = MarketplaceFactory()
        self.base_payload = [
            {
                "company_id": str(self.company.id),
                "category_id": str(self.category.id),
                "name": "Kemeja Batik Pria Premium",
                "description": "Batik Slimfit bahan katun halus, nyaman untuk kerja maupun acara formal.",
                "variant_options": [{"name": "Warna", "order": 1}, {"name": "Size", "order": 2}],
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
                        "name": "Batik Premium - Navy - L",
                        # "sku_variant_code": "1",
                        "variant_values": {"1": "Navy", "2": "L"},
                        "base_price": 180000,
                        "marketplace_listings": [
                            {
                                "marketplace_id": str(self.marketplace.id),
                                "selling_price": 210000,
                                "discounted_price": 195000,
                            }
                        ],
                    },
                    {
                        "name": "Batik Premium - Navy - XL",
                        # "sku_variant_code": "2",
                        "variant_values": {"1": "Navy", "2": "XL"},
                        "base_price": 185000,
                        "marketplace_listings": [
                            {
                                "marketplace_id": str(self.marketplace.id),
                                "selling_price": 215000,
                            }
                        ],
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
                "variant_options": [{"name": "Warna", "order": 1}, {"name": "Size", "order": 2}],
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
                        "name": "Batik Premium B - Blue - L",
                        # "sku_variant_code": "1",
                        "variant_values": {"1": "Blue", "2": "L"},
                        "base_price": 180000,
                        "marketplace_listings": [
                            {
                                "marketplace_id": str(self.marketplace.id),
                                "selling_price": 210000,
                                "discounted_price": 195000,
                            }
                        ],
                    },
                    {
                        "name": "Batik Premium B - Blue - XL",
                        # "sku_variant_code": "2",
                        "variant_values": {"1": "Blue", "2": "XL"},
                        "base_price": 185000,
                        "marketplace_listings": [
                            {
                                "marketplace_id": str(self.marketplace.id),
                                "selling_price": 215000,
                            }
                        ],
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
        # Verify 4 listings (2 per product in your payload)
        from apps.inventory.models import ProductVariantMarketplace

        self.assertEqual(ProductVariantMarketplace.objects.count(), 4)

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
        Tests that 2 products with multiple variants and listings
        are correctly mapped and saved in bulk.
        """
        # setup_data would be a fixture or dictionary containing your payload
        payload = self.base_payload + [
            {
                "company_id": str(self.company.id),
                "category_id": str(self.category.id),
                "name": "Kemeja Batik Pria Premium B",
                "description": "Batik Slimfit bahan katun halus, nyaman untuk kerja maupun acara formal.",
                "variant_options": [{"name": "Warna", "order": 1}, {"name": "Size", "order": 2}],
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
                        "name": "Batik Premium B - Blue - L",
                        # "sku_variant_code": "1",
                        "variant_values": {"1": "Blue", "2": "L"},
                        "base_price": 180000,
                        "marketplace_listings": [
                            {
                                "marketplace_id": str(self.marketplace.id),
                                "selling_price": 210000,
                                "discounted_price": 195000,
                            }
                        ],
                    },
                    {
                        "name": "Batik Premium B - Blue - XL",
                        # "sku_variant_code": "2",
                        "variant_values": {"1": "Blue", "2": "XL"},
                        "base_price": 185000,
                        "marketplace_listings": [
                            {
                                "marketplace_id": str(self.marketplace.id),
                                "selling_price": 215000,
                            }
                        ],
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
        assert ProductVariantMarketplace.objects.count() == 4

        # 3. Verify Specific Mapping (Global Indexing Check)
        # Fetch the second product to ensure it didn't get Product A's variants
        prod_b = Product.objects.get(name="Kemeja Batik Pria Premium B")
        variants_b = ProductVariant.objects.filter(product=prod_b)

        assert variants_b.count() == 2
        for variant in variants_b:
            # Verify the variant names match the 'Blue' logic in payload B
            assert "Blue" in variant.name

            # Verify Marketplace Listings are linked to these specific variants
            listings = ProductVariantMarketplace.objects.filter(product_variant=variant)
            assert listings.exists()
            assert listings.count() == 1

    def test_atomic_rollback_on_failure(self):
        """
        Tests that if data is partially corrupt (e.g., missing marketplace_id),
        NO products are created (Transaction Rollback).
        """
        payload = self.base_payload
        payload[0]["variants"][1]["marketplace_listings"][0]["marketplace_id"] = None

        self.client.post("/product/", payload, format="json")
        assert Product.objects.count() == 0


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
        """Test COGS includes allocated shipping and delivery fees per unit for single item."""
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
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 10,
                "received_qty": 10,
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
        self.assertEqual(cogs.original_qty, 10)
        self.assertEqual(cogs.remaining_qty, 10)

        unit_price_idr = 10 * 2200
        allocated_shipping = 1000
        allocated_delivery = int(100.5 * 2200)
        total_allocated = allocated_shipping + allocated_delivery
        shipping_per_unit = total_allocated / 10
        expected_cogs = unit_price_idr + shipping_per_unit

        self.assertEqual(cogs.cogs_amount, expected_cogs)
        self.assertEqual(cogs.allocated_shipping_fee, allocated_shipping)
        self.assertEqual(cogs.allocated_delivery_fee, allocated_delivery)

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
        UserProfile.objects.create(
            user=self.non_staff_user, company=self.company, role="viewer"
        )

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
