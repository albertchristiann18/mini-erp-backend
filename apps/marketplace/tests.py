from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalog.tests.factories import ProductFactory
from apps.marketplace.factories import (
    BusinessEntityFactory,
    CompanyMarketplaceFactory,
    ProductBusinessEntityFactory,
)
from apps.marketplace.models import ProductBusinessEntity
from apps.marketplace.services.business_entity_service import BusinessEntityService
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


class MigrationGraphOrderingRegressionTests(TestCase):
    """Regression tests proving the BE4b marketplace state-move migrations are correctly
    ordered: marketplace/0001_initial must always run before core/0003 (DeleteModel),
    inventory/0030 (DeleteModel), and the three noop_fk_refs retarget migrations —
    on fresh replay and on rollback — preventing the BE2 ordering-bug class from
    recurring here."""

    def test_marketplace_initial_runs_before_core_delete_marketplace(self):
        """core/0003 (DeleteModel Marketplace/MarketplaceConnection) must run after
        marketplace/0001_initial (CreateModel x5) in every forwards plan."""
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)
        core_delete_node = ("core", "0003_remove_marketplace_marketplaceconnection")
        marketplace_create_node = ("marketplace", "0001_initial")
        plan = loader.graph.forwards_plan(core_delete_node)
        self.assertIn(
            marketplace_create_node,
            plan,
            f"{marketplace_create_node} must run before {core_delete_node} or a "
            "from-scratch replay can delete the models before marketplace adopts them",
        )

    def test_marketplace_initial_runs_before_inventory_delete_models(self):
        """inventory/0030 (DeleteModel x3) must run after marketplace/0001_initial."""
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)
        inventory_delete_node = (
            "inventory",
            "0030_remove_companymarketplace_businessentity_productbusinessentity",
        )
        marketplace_create_node = ("marketplace", "0001_initial")
        plan = loader.graph.forwards_plan(inventory_delete_node)
        self.assertIn(
            marketplace_create_node,
            plan,
            f"{marketplace_create_node} must run before {inventory_delete_node} or a "
            "from-scratch replay can delete the models before marketplace adopts them",
        )

    def test_marketplace_initial_runs_before_sales_noop_fk_refs(self):
        """sales/0006_noop_fk_refs must run after marketplace/0001_initial."""
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)
        sales_retarget_node = ("sales", "0006_noop_fk_refs")
        marketplace_create_node = ("marketplace", "0001_initial")
        plan = loader.graph.forwards_plan(sales_retarget_node)
        self.assertIn(
            marketplace_create_node,
            plan,
            f"{marketplace_create_node} must run before {sales_retarget_node}",
        )

    def test_marketplace_initial_runs_before_catalog_noop_fk_refs(self):
        """catalog/0004_noop_fk_refs must run after marketplace/0001_initial."""
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)
        catalog_retarget_node = ("catalog", "0004_noop_fk_refs")
        marketplace_create_node = ("marketplace", "0001_initial")
        plan = loader.graph.forwards_plan(catalog_retarget_node)
        self.assertIn(
            marketplace_create_node,
            plan,
            f"{marketplace_create_node} must run before {catalog_retarget_node}",
        )

    def test_marketplace_initial_runs_before_shopee_noop_fk_refs(self):
        """shopee/0004_noop_fk_refs must run after marketplace/0001_initial."""
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)
        shopee_retarget_node = ("shopee", "0004_noop_fk_refs")
        marketplace_create_node = ("marketplace", "0001_initial")
        plan = loader.graph.forwards_plan(shopee_retarget_node)
        self.assertIn(
            marketplace_create_node,
            plan,
            f"{marketplace_create_node} must run before {shopee_retarget_node}",
        )

    def test_pre_delete_core_project_state_renders_without_lazy_reference_errors(self):
        """Build the project state immediately BEFORE core/0003 (DeleteModel) runs
        and force-render it — reproduces the exact crash class from BE2."""
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)
        core_delete_node = ("core", "0003_remove_marketplace_marketplaceconnection")
        state = loader.graph.make_state(
            nodes=[core_delete_node],
            at_end=False,
            real_apps=loader.unmigrated_apps,
        )
        try:
            state.apps
        except ValueError as exc:
            self.fail(
                "Rendering project state immediately before "
                f"{core_delete_node} crashed with a lazy-reference error — "
                f"missing a dependency edge: {exc}"
            )

    def test_pre_delete_inventory_project_state_renders_without_lazy_reference_errors(self):
        """Build the project state immediately BEFORE inventory/0030 (DeleteModel) runs
        and force-render it — reproduces the exact crash class from BE2."""
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection)
        inventory_delete_node = (
            "inventory",
            "0030_remove_companymarketplace_businessentity_productbusinessentity",
        )
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
                f"missing a dependency edge: {exc}"
            )
