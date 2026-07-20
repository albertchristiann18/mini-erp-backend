from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalog.tests.factories import ProductFactory
from apps.marketplace.tests.factories import (
    BusinessEntityFactory,
    CompanyMarketplaceFactory,
    ProductBusinessEntityFactory,
)
from core.factories import CompanyFactory


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
        CompanyMarketplaceFactory(company=self.company, name="Lazada")
        response = self.client.post(
            "/company-marketplaces/",
            {"name": "Lazada"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update(self):
        """PATCH /company-marketplaces/{id}/ — updates is_active."""
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
        cm = CompanyMarketplaceFactory(company=self.company, name="Shopee")
        response = self.client.delete(f"/company-marketplaces/{cm.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_delete_blocked_by_business_entity(self):
        """DELETE when BusinessEntity references it — 409."""
        cm = CompanyMarketplaceFactory(company=self.company, name="Shopee")
        BusinessEntityFactory(company=self.company, marketplace=cm)
        response = self.client.delete(f"/company-marketplaces/{cm.id}/")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)


class TestBusinessEntityAPI(APITestCase):
    """Tests for BusinessEntity and ProductBusinessEntity API endpoints."""

    def setUp(self):
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
