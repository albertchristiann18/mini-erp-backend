from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalog.factories import (
    CategoryFactory,
    ProductDimensionImageFactory,
    ProductFactory,
    ProductPhotoFactory,
    ProductVariantFactory,
)
from apps.catalog.models import Category, Product, ProductDimensionImage, ProductVariant
from apps.purchasing.factories import ProductSupplierFactory, SupplierFactory
from apps.purchasing.models import ProductSupplier
from core.factories import CompanyFactory, WarehouseFactory
from core.permissions import IsStaffOrReadOnly as StaffPerm
from core.utils import generate_ulid

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
        from apps.catalog.serializers import ProductVariantStockSerializer

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
        from apps.catalog.serializers import ProductVariantStockSerializer

        product = ProductFactory(
            company=self.company,
            category=self.category,
        )
        variant = ProductVariantFactory(product=product)
        serializer = ProductVariantStockSerializer(variant)
        self.assertIn("product_photo_url", serializer.data)


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
        from apps.catalog.serializers import VariantSerializer

        variant = ProductVariantFactory(company=self.company)
        serializer = VariantSerializer(variant)
        self.assertIsNone(serializer.data["photo_url"])

    def test_product_photo_url_falls_back_to_product_photo(self):
        """ProductVariantStockSerializer falls back to product photo when variant has none."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.catalog.serializers import ProductVariantStockSerializer

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

        from apps.catalog.serializers import ProductVariantStockSerializer

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
        from apps.catalog.serializers import ProductVariantStockSerializer

        product = ProductFactory(company=self.company, category=self.category)
        variant = ProductVariantFactory(product=product, company=self.company)
        serializer = ProductVariantStockSerializer(variant)
        self.assertIsNone(serializer.data["product_supplier_link"])

    def test_product_supplier_link_returns_first_supplier_link(self):
        """product_supplier_link returns first ProductSupplier link without supplier_id filter."""
        from apps.catalog.serializers import ProductVariantStockSerializer

        product = ProductFactory(company=self.company, category=self.category)
        variant = ProductVariantFactory(product=product, company=self.company)
        ProductSupplierFactory(
            product=product, company=self.company, supplier_link="https://example.com"
        )
        serializer = ProductVariantStockSerializer(variant)
        self.assertEqual(serializer.data["product_supplier_link"], "https://example.com")

    def test_product_supplier_link_filtered_by_supplier_id(self):
        """product_supplier_link respects supplier_id query param."""
        from apps.catalog.serializers import ProductVariantStockSerializer

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
        created = ProductDimensionImage.objects.get(
            product=self.product, dim_key="Warna", dim_value="White"
        )
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
        ProductDimensionImageFactory(product=self.product, dim_key="Warna", dim_value="White")
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
        ProductDimensionImageFactory(product=self.product, dim_key="Warna", dim_value="White")
        url = f"/product/{self.product.id}/photo-proxy/?dim_key=Warna&dim_value=White"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_photo_proxy_falls_back_to_gallery_when_no_dimension_image_matches(self):
        from apps.catalog.factories import ProductPhotoFactory

        ProductPhotoFactory(product=self.product, company=self.company, order=0)
        url = f"/product/{self.product.id}/photo-proxy/?dim_key=Warna&dim_value=NonExistent"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_dimension_image_upload_missing_dim_key_returns_400(self):
        photo = SimpleUploadedFile("w.jpg", b"imgdata", content_type="image/jpeg")
        url = f"/product/{self.product.id}/dimension-image/"
        response = self.client.post(url, {"dim_value": "White", "photo": photo}, format="multipart")
        self.assertEqual(response.status_code, 400)

    def test_dimension_image_upload_missing_dim_value_returns_400(self):
        photo = SimpleUploadedFile("w.jpg", b"imgdata", content_type="image/jpeg")
        url = f"/product/{self.product.id}/dimension-image/"
        response = self.client.post(url, {"dim_key": "Warna", "photo": photo}, format="multipart")
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

    def test_dimension_image_upload_rejected_when_dim_key_not_configured_on_product(self):
        photo = SimpleUploadedFile("c.jpg", b"imgdata", content_type="image/jpeg")
        url = f"/product/{self.product.id}/dimension-image/"
        response = self.client.post(
            url,
            {"dim_key": "Color", "dim_value": "White", "photo": photo},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Color", response.data["error"])

    def test_dimension_image_upload_allowed_when_product_has_no_configured_dims(self):
        product_no_dims = ProductFactory(company=self.company, category=self.category)
        photo = SimpleUploadedFile("x.jpg", b"imgdata", content_type="image/jpeg")
        url = f"/product/{product_no_dims.id}/dimension-image/"
        response = self.client.post(
            url,
            {"dim_key": "AnyKey", "dim_value": "AnyValue", "photo": photo},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200)

    def test_dimension_image_delete_strips_whitespace_from_dim_key_and_value(self):
        ProductDimensionImageFactory(product=self.product, dim_key="Warna", dim_value="White")
        url = f"/product/{self.product.id}/dimension-image/"
        response = self.client.delete(
            url, {"dim_key": " Warna ", "dim_value": " White "}, format="json"
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            ProductDimensionImage.objects.filter(
                product=self.product, dim_key="Warna", dim_value="White"
            ).exists()
        )

    def test_product_detail_dimension_images_returns_proxy_urls(self):
        ProductDimensionImageFactory(product=self.product, dim_key="Warna", dim_value="White")
        response = self.client.get(f"/product/{self.product.id}/")
        self.assertEqual(response.status_code, 200)
        dim_images = response.data["dimension_images"]
        self.assertEqual(len(dim_images), 1)
        photo_url = dim_images[0]["photo_url"]
        self.assertIsNotNone(photo_url)
        self.assertIn("photo-proxy", photo_url)
        self.assertIn("dim_key=Warna", photo_url)
        self.assertIn("dim_value=White", photo_url)

    def test_changing_dim1_key_removes_orphan_dimension_images(self):
        ProductDimensionImageFactory(product=self.product, dim_key="Warna", dim_value="White")
        self.assertEqual(
            ProductDimensionImage.objects.filter(product=self.product, dim_key="Warna").count(), 1
        )
        url = f"/product/{self.product.id}/"
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch(url, {"dim1_key": "Color"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            ProductDimensionImage.objects.filter(product=self.product, dim_key="Warna").count(), 0
        )


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


class SKUTriggerRegressionTests(TestCase):
    """Regression tests for trigger functions after renaming tables to master_*.

    These tests verify that the PL/pgSQL trigger functions that auto-generate SKU codes
    still fire correctly after the table rename migrations (catalog/0003, inventory/0027).
    """

    def setUp(self):
        self.company = CompanyFactory()
        self.category = CategoryFactory(company=self.company, category_code="TSH")

    def test_product_sku_trigger_fires_against_renamed_category_table(self):
        """DB trigger auto-generates Product.sku_code by reading master_category."""
        # Create a valid product via ORM (gets a real sku_code from the trigger)
        template = ProductFactory(company=self.company, category=self.category)
        # The trigger mutates sku_code inside Postgres via a plain INSERT with no
        # RETURNING clause -- Django never re-reads it back into the in-memory
        # instance, so it must be explicitly refreshed before asserting on it.
        template.refresh_from_db()
        self.assertIsNotNone(template.sku_code)
        self.assertTrue(template.sku_code.startswith("TSH-"))

        # Read the template row's actual columns from the database. product_id is a
        # Postgres uuid column (ULIDField.get_internal_type() == "UUIDField") -- pass
        # the real uuid.UUID via .uuid, not str(), which yields the 26-char base32
        # ULID form Postgres cannot cast to uuid.
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM master_product WHERE product_id = %s", [template.pk.uuid])
            columns = [col[0] for col in cursor.description]
            values = list(cursor.fetchone())

        # Clone the row: map column names to values
        col_value_map = dict(zip(columns, values))
        new_pk = generate_ulid().uuid  # same uuid-cast requirement as above
        col_value_map["product_id"] = new_pk
        col_value_map["sku_code"] = ""  # Blank triggers the DB trigger function
        col_value_map["name"] = "Trigger Test Product Master Rename"

        # Insert via raw SQL, bypassing ORM
        cols = list(col_value_map.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO master_product ({', '.join(cols)}) VALUES ({placeholders})",
                list(col_value_map.values()),
            )
            # Verify the trigger generated a sku_code
            cursor.execute("SELECT sku_code FROM master_product WHERE product_id = %s", [new_pk])
            result_sku_code = cursor.fetchone()[0]

        # The trigger should have read master_category and generated a code
        self.assertIsNotNone(result_sku_code)
        self.assertTrue(result_sku_code.startswith(self.category.category_code.upper()))

    def test_variant_sku_trigger_fires_against_renamed_product_table(self):
        """DB trigger auto-generates ProductVariant.sku_variant_code by reading master_product."""
        # Create a valid variant via ORM (gets a real sku_variant_code from the trigger)
        product = ProductFactory(company=self.company, category=self.category)
        product.refresh_from_db()  # re-read the trigger-generated sku_code before using it below
        template = ProductVariantFactory(product=product, sku_variant_code="")
        template.refresh_from_db()
        self.assertIsNotNone(template.sku_variant_code)
        self.assertTrue(template.sku_variant_code.startswith(product.sku_code))

        # Read the template row's actual columns from the database. product_variant_id is
        # a Postgres uuid column -- pass the real uuid.UUID via .uuid, not str(), which
        # yields the 26-char base32 ULID form Postgres cannot cast to uuid.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM master_productvariant WHERE product_variant_id = %s",
                [template.pk.uuid],
            )
            columns = [col[0] for col in cursor.description]
            values = list(cursor.fetchone())

        # Clone the row, but point it at a SECOND, independently-created parent product.
        # generate_variant_sku() has no uniqueness source of its own (unlike
        # generate_product_sku(), which uses product_sku_seq) -- with variant_values={} on
        # both rows, jsonb_each_text('{}'::jsonb) returns zero rows so string_agg yields NULL
        # and the suffix branch never fires, meaning the generated sku_variant_code is always
        # exactly the parent's bare sku_code. Cloning against the SAME parent as `template`
        # would therefore always collide on ProductVariant.sku_variant_code's unique
        # constraint. Using a different parent (whose sku_code is guaranteed unique via
        # Product's own sequence-backed trigger) makes the two generated codes provably
        # distinct without relying on any jsonb suffix behavior.
        second_product = ProductFactory(company=self.company, category=self.category)
        second_product.refresh_from_db()

        # Clone the row: map column names to values
        col_value_map = dict(zip(columns, values))
        new_pk = generate_ulid().uuid  # same uuid-cast requirement as above
        col_value_map["product_variant_id"] = new_pk
        col_value_map["product_id"] = second_product.pk.uuid  # different parent, same reason
        col_value_map["sku_variant_code"] = ""  # Blank triggers the DB trigger function
        col_value_map["name"] = "Trigger Test Variant Master Rename"

        # Insert via raw SQL, bypassing ORM
        cols = list(col_value_map.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO master_productvariant ({', '.join(cols)}) VALUES ({placeholders})",
                list(col_value_map.values()),
            )
            # Verify the trigger generated a sku_variant_code
            cursor.execute(
                "SELECT sku_variant_code FROM master_productvariant WHERE product_variant_id = %s",
                [new_pk],
            )
            result_sku_variant_code = cursor.fetchone()[0]

        # The trigger should have read master_product for THIS row's own product_id
        # (second_product, not the original `product`) and generated a matching code.
        self.assertIsNotNone(result_sku_variant_code)
        self.assertTrue(result_sku_variant_code.startswith(second_product.sku_code))
        self.assertNotEqual(result_sku_variant_code, template.sku_variant_code)


class DatabaseSchemaRegressionTests(TestCase):
    """Tests to verify the master_* table renaming was successful at the schema level."""

    def test_table_names_updated_in_django_meta(self):
        """Verify that all 6 models have their db_table set to master_*."""
        # Catalog models
        self.assertEqual(Category._meta.db_table, "master_category")
        self.assertEqual(Product._meta.db_table, "master_product")
        from apps.catalog.models import ProductPhoto, ProductVariantMarketplace

        self.assertEqual(ProductPhoto._meta.db_table, "master_productphoto")
        self.assertEqual(ProductVariant._meta.db_table, "master_productvariant")
        self.assertEqual(
            ProductVariantMarketplace._meta.db_table, "master_productvariantmarketplace"
        )

        # Inventory model
        from apps.inventory.models import Warehouse

        self.assertEqual(Warehouse._meta.db_table, "master_warehouse")

    def test_old_tables_absent_new_tables_present_in_db(self):
        """Verify that physical tables in the database use the new master_* names."""
        expected_tables = [
            "master_category",
            "master_product",
            "master_productphoto",
            "master_productvariant",
            "master_productvariantmarketplace",
            "master_warehouse",
        ]
        old_tables = [
            "inventory_category",
            "inventory_product",
            "product_photo",
            "inventory_productvariant",
            "inventory_productvariantmarketplace",
            "inventory_warehouse",
        ]

        with connection.cursor() as cursor:
            # Check that all new tables exist
            for table_name in expected_tables:
                cursor.execute(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = %s AND table_schema = 'public')",
                    [table_name],
                )
                self.assertTrue(
                    cursor.fetchone()[0],
                    f"New table {table_name} not found in database",
                )

            # Check that all old tables are gone
            for table_name in old_tables:
                cursor.execute(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = %s AND table_schema = 'public')",
                    [table_name],
                )
                self.assertFalse(
                    cursor.fetchone()[0], f"Old table {table_name} still exists in database"
                )


class MigrationGraphOrderingRegressionTests(TestCase):
    """Regression tests proving the master_* rename migrations (catalog/0003,
    inventory/0027) cannot be scheduled before any migration that creates a real
    FK constraint against the pre-rename table names. Without these edges, Django's
    migration plan is under-constrained and a from-scratch replay can (non-
    deterministically) sequence the rename before a deferred FK-creation statement
    fires, producing "relation ... does not exist" — the bug fixed in this pass."""

    def test_catalog_master_table_rename_runs_after_all_fk_creating_migrations(self):
        loader = MigrationLoader(connection)
        plan = loader.graph.forwards_plan(("catalog", "0003_rename_master_tables"))
        for dependency in [
            ("sales", "0001_initial"),
            ("purchasing", "0001_initial"),
            ("shopee", "0002_add_shopee_stock_sync_log"),
        ]:
            self.assertIn(
                dependency,
                plan,
                f"{dependency} must run before catalog/0003_rename_master_tables or its "
                "deferred FK constraint SQL will reference an already-renamed table",
            )

    def test_inventory_warehouse_rename_runs_after_all_fk_creating_migrations(self):
        loader = MigrationLoader(connection)
        plan = loader.graph.forwards_plan(("inventory", "0027_rename_warehouse_table"))
        for dependency in [
            ("sales", "0001_initial"),
            ("purchasing", "0001_initial"),
            ("shopee", "0001_initial"),
            ("tiktok", "0001_initial"),
        ]:
            self.assertIn(
                dependency,
                plan,
                f"{dependency} must run before inventory/0027_rename_warehouse_table or its "
                "deferred FK constraint SQL will reference an already-renamed table",
            )

    def test_catalog_master_table_rename_runs_after_fk_retargeting_migrations(self):
        """catalog/0003 must run after the four 'noop_fk_refs' migrations that
        retarget ProductBusinessEntity/ProductCogs/ProductDimensionImage/
        ProductSupplier/ProductVariantWarehouse/StockMovement/PurchaseOrderDetail/
        SalesOrderItem/SalesReturnItem/ShopeeStockSyncLog's FK fields away from the
        inventory.product/inventory.productvariant models inventory/0025 deletes.
        Without this edge, a from-scratch topological replay can sequence
        catalog/0003 before one of these siblings, leaving a dangling lazy FK
        reference at the exact point Django force-renders state during a
        backward migration plan."""
        loader = MigrationLoader(connection)
        plan = loader.graph.forwards_plan(("catalog", "0003_rename_master_tables"))
        for dependency in [
            ("inventory", "0026_noop_fk_refs"),
            ("purchasing", "0025_noop_fk_refs"),
            ("sales", "0004_noop_fk_refs"),
            ("shopee", "0003_noop_fk_refs"),
        ]:
            self.assertIn(
                dependency,
                plan,
                f"{dependency} must run before catalog/0003_rename_master_tables or a "
                "backward migration plan can force-render project state while this "
                "sibling's FK retargeting hasn't happened yet, crashing with a lazy "
                "reference to a deleted inventory model",
            )

    def test_catalog_master_table_rename_predecessor_state_renders_without_lazy_reference_errors(
        self,
    ):
        """Reproduces the exact crash from the original bug: build the project
        state as it exists immediately BEFORE catalog/0003_rename_master_tables
        runs (the same computation Django's backward executor performs and
        force-validates at that point) and force a full render. Before the fix
        in this pass, this crashed with 'declared with a lazy reference to
        inventory.product, but app inventory doesn't provide model product' —
        this test exercises the actual backward-plan render logic, not just
        forwards_plan membership, so this class of bug is caught by CI."""
        loader = MigrationLoader(connection)
        state = loader.graph.make_state(
            nodes=[("catalog", "0003_rename_master_tables")],
            at_end=False,
            real_apps=loader.unmigrated_apps,
        )
        try:
            state.apps
        except ValueError as exc:
            self.fail(
                "Rendering project state immediately before "
                "catalog/0003_rename_master_tables crashed with a lazy-reference "
                "error — catalog/0003 is missing a dependency edge on one of the "
                "FK-retargeting 'noop_fk_refs' migrations (inventory/0026, "
                f"purchasing/0025, sales/0004, shopee/0003): {exc}"
            )

    def test_catalog_create_productdimensionimage_runs_before_inventory_delete(self):
        """catalog/0005_state_only_create_productdimensionimage must be ordered before
        inventory/0031_state_only_delete_productdimensionimage in the forward plan.
        Without this edge a from-scratch replay could delete the model from inventory
        state before catalog has adopted it, leaving a dangling lazy FK reference."""
        loader = MigrationLoader(connection)
        plan = loader.graph.forwards_plan(
            ("inventory", "0031_state_only_delete_productdimensionimage")
        )
        self.assertIn(
            ("catalog", "0005_state_only_create_productdimensionimage"),
            plan,
            "catalog/0005 must run before inventory/0031 or the DeleteModel runs "
            "before catalog has registered ProductDimensionImage, breaking lazy FK "
            "resolution on a from-scratch replay",
        )

    def test_inventory_delete_productdimensionimage_predecessor_state_renders_without_lazy_reference_errors(
        self,
    ):
        """Build the project state immediately BEFORE inventory/0031 runs and force a
        full render. This catches the BE2-class bug where a DeleteModel is scheduled
        before the adopting app's CreateModel, producing a lazy-reference crash at
        render time. The state at that point must have ProductDimensionImage owned by
        catalog (via catalog/0005) and still present in inventory (not yet deleted)."""
        loader = MigrationLoader(connection)
        state = loader.graph.make_state(
            nodes=[("inventory", "0031_state_only_delete_productdimensionimage")],
            at_end=False,
            real_apps=loader.unmigrated_apps,
        )
        try:
            state.apps
        except ValueError as exc:
            self.fail(
                "Rendering project state immediately before "
                "inventory/0031_state_only_delete_productdimensionimage crashed with a "
                "lazy-reference error — inventory/0031 is missing the dependency edge on "
                f"catalog/0005_state_only_create_productdimensionimage: {exc}"
            )
