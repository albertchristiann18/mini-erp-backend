from django.test import TestCase

from apps.catalog.tests.factories import ProductFactory
from apps.marketplace.models import ProductBusinessEntity
from apps.marketplace.services.business_entity_service import BusinessEntityService
from apps.marketplace.tests.factories import (
    BusinessEntityFactory,
    CompanyMarketplaceFactory,
    ProductBusinessEntityFactory,
)
from core.factories import CompanyFactory


class TestBusinessEntityService(TestCase):
    """Tests for BusinessEntityService — service-level business logic."""

    def setUp(self):
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
        from apps.catalog.models import Product

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
        CompanyMarketplaceFactory(company=self.company, name="Shopee")
        CompanyMarketplaceFactory(company=self.company, name="TikTok")
        result = self.service.get_or_seed_company_marketplaces(str(self.company.id))
        self.assertEqual(len(result), 2)

    def test_get_or_seed_returns_existing_custom(self):
        """Company has Shopee, TikTok, Lazada — returns all 3, no new ones."""
        CompanyMarketplaceFactory(company=self.company, name="Shopee")
        CompanyMarketplaceFactory(company=self.company, name="TikTok")
        CompanyMarketplaceFactory(company=self.company, name="Lazada")
        result = self.service.get_or_seed_company_marketplaces(str(self.company.id))
        self.assertEqual(len(result), 3)
        names = sorted(m.name for m in result)
        self.assertEqual(names, ["Lazada", "Shopee", "TikTok"])
