from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.catalog.tests.factories import (
    CategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.purchasing.models import PurchaseOrder
from apps.purchasing.serializers import PurchaseOrderUpdateSerializer
from apps.purchasing.services.purchasing_service import PurchaseOrderService
from apps.purchasing.tests.factories import (
    PurchaseOrderDetailFactory,
    PurchaseOrderFactory,
)
from core.factories import CompanyFactory, WarehouseFactory


class PurchaseOrderSerializerValidationTest(TestCase):
    """Test cases for PurchaseOrderUpdateSerializer validation"""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.service = PurchaseOrderService()

    def _create_serializer(self, po, data, partial=False):
        return PurchaseOrderUpdateSerializer(po, data=data, partial=partial)

    def test_draft_to_ordered_requires_exchange_rate(self):
        """Test that transitioning DRAFT to ORDERED requires exchange_rate"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.ORDERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("exchange_rate", serializer.errors)

    def test_draft_to_ordered_requires_invoice_file(self):
        """Test that transitioning DRAFT to ORDERED requires invoice file"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.ORDERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("purchase_order_invoice_file", serializer.errors)

    def test_draft_to_ordered_requires_invoice_number(self):
        """Test that transitioning DRAFT to ORDERED requires invoice_number"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            purchase_order_invoice_file="test.pdf",
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.ORDERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("invoice_number", serializer.errors)

    def test_draft_to_ordered_requires_invoice_date(self):
        """Test that transitioning DRAFT to ORDERED requires invoice_date"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            purchase_order_invoice_file="test.pdf",
            invoice_number="INV-001",
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.ORDERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("invoice_date", serializer.errors)

    def test_draft_to_ordered_requires_order_details(self):
        """Test that transitioning DRAFT to ORDERED requires order_details when none exist"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            purchase_order_invoice_file="test.pdf",
            invoice_number="INV-001",
            invoice_date=date.today(),
            commission_fee_pct=Decimal("10"),
            forwarder_name="Test Forwarder",
            supplier_name="Test Supplier",
            shop_services="Test Shop Service",
            delivery_fee=Decimal("0"),
        )

        serializer = self._create_serializer(
            po,
            {
                "status": PurchaseOrder.POStatus.ORDERED,
            },
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("order_details", serializer.errors)

    def test_draft_to_ordered_requires_commission_fee_pct(self):
        """Test that transitioning DRAFT to ORDERED requires commission_fee_pct"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            purchase_order_invoice_file="test.pdf",
            invoice_number="INV-001",
            invoice_date=date.today(),
            forwarder_name="Test Forwarder",
            supplier_name="Test Supplier",
            shop_services="Test Shop Services",
            commission_fee_pct=None,
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.ORDERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("commission_fee_pct", serializer.errors)

    def test_draft_to_ordered_requires_forwarder_name(self):
        """Test that transitioning DRAFT to ORDERED requires forwarder_name"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            purchase_order_invoice_file="test.pdf",
            invoice_number="INV-001",
            invoice_date=date.today(),
            commission_fee_pct=Decimal("10"),
            forwarder_name="",
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.ORDERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("forwarder_name", serializer.errors)

    def test_draft_to_ordered_requires_supplier_name(self):
        """Test that transitioning DRAFT to ORDERED requires supplier_name"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            purchase_order_invoice_file="test.pdf",
            invoice_number="INV-001",
            invoice_date=date.today(),
            commission_fee_pct=Decimal("10"),
            forwarder_name="Test Forwarder",
            shop_services="Test Shop Services",
            supplier_name="",
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.ORDERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("supplier_name", serializer.errors)

    def test_draft_to_ordered_requires_shop_services(self):
        """Test that transitioning DRAFT to ORDERED requires shop_services"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            purchase_order_invoice_file="test.pdf",
            invoice_number="INV-001",
            invoice_date=date.today(),
            commission_fee_pct=Decimal("10"),
            forwarder_name="Test Forwarder",
            supplier_name="Test Supplier",
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.ORDERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("shop_services", serializer.errors)

    def test_draft_to_ordered_requires_delivery_fee(self):
        """Test that transitioning DRAFT to ORDERED requires delivery_fee (can be 0)"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            exchange_rate=2200,
            purchase_order_invoice_file="test.pdf",
            invoice_number="INV-001",
            invoice_date=date.today(),
            commission_fee_pct=Decimal("10"),
            forwarder_name="Test Forwarder",
            supplier_name="Test Supplier",
            shop_services="Test Shop Service",
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.ORDERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("delivery_fee", serializer.errors)

    def test_draft_to_ordered_allows_zero_delivery_fee(self):
        """Test that transitioning DRAFT to ORDERED allows delivery_fee=0"""
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
            forwarder_name="Test Forwarder",
            supplier_name="Test Supplier",
            shop_services="Test Shop Service",
            delivery_fee=Decimal("0"),
        )
        PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
        )

        serializer = self._create_serializer(
            po,
            {
                "status": PurchaseOrder.POStatus.ORDERED,
                "order_details": [
                    {
                        "product_variant_id": str(self.product_variant.id),
                        "ordered_qty": 100,
                    }
                ],
            },
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_ordered_to_shipped_requires_delivery_order_number(self):
        """Test that transitioning ORDERED to SHIPPED requires delivery order number"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.SHIPPED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("delivery_order_number", serializer.errors)

    def test_ordered_to_shipped_requires_delivery_order_file(self):
        """Test that transitioning ORDERED to SHIPPED requires delivery order file"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
            delivery_order_number="DO-001",
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.SHIPPED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("delivery_order_file", serializer.errors)

    def test_ordered_to_shipped_requires_shipping_fee(self):
        """Test that transitioning ORDERED to SHIPPED requires shipping_fee"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
            delivery_order_number="DO-001",
            delivery_order_file="existing_file.pdf",
            cbm=Decimal("1.5"),
            weight=Decimal("10.0"),
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.SHIPPED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("shipping_fee_per_cbm", serializer.errors)

    def test_ordered_to_shipped_requires_cbm(self):
        """Test that transitioning ORDERED to SHIPPED requires cbm"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
            delivery_order_number="DO-001",
            delivery_order_file="existing_file.pdf",
            shipping_fee_per_cbm=1000,
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.SHIPPED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("cbm", serializer.errors)

    def test_ordered_to_shipped_requires_weight(self):
        """Test that transitioning ORDERED to SHIPPED requires weight"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
            delivery_order_number="DO-001",
            delivery_order_file="existing_file.pdf",
            shipping_fee_per_cbm=1000,
            cbm=Decimal("1.5"),
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.SHIPPED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("weight", serializer.errors)

    def test_shipped_to_delivered_requires_delivery_order_invoice(self):
        """Test that transitioning SHIPPED to DELIVERED requires DO invoice"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            delivery_order_number="DO-001",
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.DELIVERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("delivery_order_invoice_file", serializer.errors)

    def test_invalid_status_transition_raises_error(self):
        """Test that invalid status transition raises error"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )

        serializer = self._create_serializer(po, {"status": PurchaseOrder.POStatus.DELIVERED})

        self.assertFalse(serializer.is_valid())
        self.assertIn("status", serializer.errors)

    def test_exchange_rate_cannot_change_after_ordered(self):
        """Test that exchange_rate cannot be changed after ORDERED status"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
            exchange_rate=2200,
        )

        serializer = self._create_serializer(po, {"exchange_rate": 2300}, partial=True)

        self.assertFalse(serializer.is_valid())
        self.assertIn("exchange_rate", serializer.errors)

    def test_same_exchange_rate_allowed_on_non_draft(self):
        """Test sending the same exchange_rate on ORDERED PO does not raise error"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
            exchange_rate=Decimal("2200"),
        )

        serializer = self._create_serializer(po, {"exchange_rate": Decimal("2200")}, partial=True)

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_unit_price_foreign_cannot_change_after_ordered(self):
        """Test that unit_price_foreign cannot be changed after ORDERED status"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
            unit_price_foreign=Decimal("10"),
        )

        po.refresh_from_db()
        with self.assertRaises(Exception) as context:
            self.service.update_purchase_order(
                po,
                {
                    "order_details": [
                        {
                            "id": str(detail.id),
                            "ordered_qty": 100,
                            "unit_price_foreign": Decimal("15"),
                        }
                    ]
                },
            )

        self.assertIn("order_details", str(context.exception))
        self.assertIn("unit_price_foreign", str(context.exception))

    def test_discounted_unit_price_foreign_cannot_change_after_ordered(self):
        """Test that discounted_unit_price_foreign cannot be changed after ORDERED status"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
            exchange_rate=2200,
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
            unit_price_foreign=Decimal("10"),
            discounted_unit_price_foreign=Decimal("8"),
        )

        po.refresh_from_db()
        with self.assertRaises(Exception) as context:
            self.service.update_purchase_order(
                po,
                {
                    "order_details": [
                        {
                            "id": str(detail.id),
                            "ordered_qty": 100,
                            "unit_price_foreign": Decimal("10"),
                            "discounted_unit_price_foreign": Decimal("6"),
                        }
                    ]
                },
            )

        self.assertIn("order_details", str(context.exception))
        self.assertIn("discounted_unit_price_foreign", str(context.exception))

    def test_received_qty_cannot_be_filled_if_status_not_shipped_or_delivered(self):
        """Test that received_qty cannot be filled when status is not SHIPPED or DELIVERED"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            currency="USD",
            exchange_rate=2200,
            purchase_order_invoice_file="invoice.pdf",
            invoice_number="INV-001",
            invoice_date=date.today(),
            supplier_name="Supplier",
            forwarder_name="Forwarder",
            shop_services="service",
            commission_fee_pct=Decimal("10"),
            delivery_fee=100,
        )

        serializer = self._create_serializer(
            po,
            {
                "status": PurchaseOrder.POStatus.ORDERED,
                "order_details": [
                    {
                        "product_variant_id": str(self.product_variant.id),
                        "ordered_qty": 100,
                        "received_qty": 50,
                        "unit_price_foreign": Decimal("10"),
                    }
                ],
            },
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("order_details", serializer.errors)
        self.assertIn("received_qty", str(serializer.errors["order_details"]))

    def test_received_qty_allowed_when_status_is_shipped(self):
        """Test that received_qty can be filled when status is SHIPPED"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.ORDERED,
            exchange_rate=2200,
            delivery_order_number="DO-001",
            delivery_order_file="existing_file.pdf",
            shipping_fee_per_cbm=100,
            cbm=Decimal("1"),
            weight=Decimal("10"),
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
            unit_price_foreign=Decimal("10"),
        )
        po.refresh_from_db()

        serializer = self._create_serializer(
            po,
            {
                "status": PurchaseOrder.POStatus.SHIPPED,
                "order_details": [
                    {
                        "id": str(detail.id),
                        "product_variant_id": str(self.product_variant.id),
                        "received_qty": 50,
                    }
                ],
            },
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_received_qty_allowed_when_status_is_delivered(self):
        """Test that received_qty can be filled when status is DELIVERED"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
            delivery_order_invoice_file="existing_file.pdf",
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
            unit_price_foreign=Decimal("10"),
        )
        po.refresh_from_db()

        serializer = self._create_serializer(
            po,
            {
                "status": PurchaseOrder.POStatus.DELIVERED,
                "order_details": [
                    {
                        "id": str(detail.id),
                        "product_variant_id": str(self.product_variant.id),
                        "received_qty": 50,
                    }
                ],
            },
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_cannot_add_new_details_when_shipped(self):
        """Test that adding new details fails when status is SHIPPED"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
            unit_price_foreign=Decimal("10"),
        )

        product2 = ProductFactory(category=self.category, company=self.company)
        product_variant2 = ProductVariantFactory(product=product2)

        po.refresh_from_db()

        serializer = self._create_serializer(
            po,
            {
                "order_details": [
                    {
                        "id": str(detail.id),
                        "product_variant_id": str(self.product_variant.id),
                    },
                    {
                        "product_variant_id": str(product_variant2.id),
                    },
                ],
            },
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("order_details", serializer.errors)
        self.assertIn("Cannot add new details", str(serializer.errors["order_details"]))

    def test_cannot_change_ordered_qty_from_draft_to_ordered(self):
        """Test that ordered_qty cannot be changed when transitioning from DRAFT to ORDERED"""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
            currency="USD",
            exchange_rate=2200,
            forwarder_name="Test Forwarder",
            shop_services="Test Service",
            commission_fee_pct=Decimal("10"),
            delivery_fee=100,
            purchase_order_invoice_file="invoice.pdf",
            invoice_number="INV-001",
            invoice_date=date.today(),
            supplier_name="Supplier",
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=self.product_variant,
            ordered_qty=100,
            unit_price_foreign=Decimal("10"),
        )

        po.refresh_from_db()

        serializer = self._create_serializer(
            po,
            {
                "status": PurchaseOrder.POStatus.ORDERED,
                "order_details": [
                    {
                        "id": str(detail.id),
                        "product_variant_id": str(self.product_variant.id),
                        "ordered_qty": 150,
                        "unit_price_foreign": Decimal("10"),
                    },
                ],
            },
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("order_details", serializer.errors)
        self.assertIn("ordered_qty", str(serializer.errors["order_details"]))


class POListSerializerExpansionTest(TestCase):
    """Tests for expanded PurchaseOrderListSerializer fields and filters"""

    def setUp(self):
        from core.models import UserProfile

        self.client = APIClient()
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)

        user = User.objects.create_user(username="staff", password="password", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)

    def test_list_includes_cost_ratio_cogs(self):
        """GET /purchase-order/ should include cost_ratio_cogs in results"""
        PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            procure_amount=1000,
            shipping_fee=500,
            total_item_amount=10000,
        )

        response = self.client.get("/purchase-order/", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("cost_ratio_cogs", response.data["results"][0])

    def test_list_includes_shipping_per_qty(self):
        """GET /purchase-order/ should include shipping_per_qty in results"""
        PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            shipping_fee=1000,
            total_ordered_qty=100,
        )

        response = self.client.get("/purchase-order/", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("shipping_per_qty", response.data["results"][0])

    def test_list_includes_forwarder_name(self):
        """GET /purchase-order/ should include forwarder_name in results"""
        PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            forwarder_name="Test Forwarder",
        )

        response = self.client.get("/purchase-order/", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("forwarder_name", response.data["results"][0])

    def test_list_filter_by_date_from(self):
        """Filter by date_from should return only POs with invoice_date >= date_from"""
        po1 = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            invoice_date=date(2026, 5, 1),
        )
        po2 = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            invoice_date=date(2026, 5, 15),
        )

        response = self.client.get("/purchase-order/", {"date_from": "2026-05-10"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result_ids = [item["id"] for item in response.data["results"]]
        self.assertNotIn(str(po1.id), result_ids)
        self.assertIn(str(po2.id), result_ids)

    def test_list_filter_by_forwarder(self):
        """Filter by forwarder should return only POs with matching forwarder_name"""
        po1 = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            forwarder_name="Forwarder A",
        )
        po2 = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            forwarder_name="Forwarder B",
        )

        response = self.client.get("/purchase-order/", {"forwarder": "Forwarder A"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result_ids = [item["id"] for item in response.data["results"]]
        self.assertIn(str(po1.id), result_ids)
        self.assertNotIn(str(po2.id), result_ids)


class PurchaseOrderDetailSerializerProductFieldsTest(TestCase):
    """Tests for product_id, product_name, product_supplier_link in PurchaseOrderDetailSerializer."""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(
            company=self.company,
            category=self.category,
        )
        self.product_variant = ProductVariantFactory(product=self.product)
        self.po = PurchaseOrderFactory(warehouse=self.warehouse, company=self.company)
        self.detail = PurchaseOrderDetailFactory(
            purchase_order=self.po,
            product_variant=self.product_variant,
            supplier_link="https://example.com/supplier/product-789",
        )

    def test_po_detail_serializer_includes_product_fields(self):
        from apps.purchasing.serializers import PurchaseOrderDetailSerializer

        serializer = PurchaseOrderDetailSerializer(self.detail)
        self.assertIn("product_id", serializer.data)
        self.assertIn("product_name", serializer.data)
        self.assertIn("product_supplier_link", serializer.data)
        self.assertEqual(serializer.data["product_id"], str(self.product.id))
        self.assertEqual(serializer.data["product_name"], self.product.name)
        self.assertEqual(
            serializer.data["product_supplier_link"], "https://example.com/supplier/product-789"
        )

    def test_po_detail_serializer_includes_sku_variant_code(self):
        from apps.purchasing.serializers import PurchaseOrderDetailSerializer

        serializer = PurchaseOrderDetailSerializer(self.detail)
        self.assertIn("sku_variant_code", serializer.data)
        self.assertEqual(serializer.data["sku_variant_code"], self.product_variant.sku_variant_code)
