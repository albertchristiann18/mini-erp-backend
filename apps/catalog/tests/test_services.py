from django.test import TestCase

from apps.catalog.tests.factories import CategoryFactory, ProductFactory, ProductVariantFactory
from core.factories import CompanyFactory


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
