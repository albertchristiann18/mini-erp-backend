import json
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalog.models import Category, Product, ProductVariant
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductVariantFactory,
    ProductVariantMarketplaceFactory,
)
from apps.inventory.models import ProductVariantWarehouse, StockMovement
from apps.inventory.services.inventory_service import InventoryService
from apps.inventory.tests.factories import ProductCogsFactory, ProductVariantWarehouseFactory
from apps.marketplace.shopee.client import ShopeeClient
from apps.marketplace.shopee.exceptions import ShopeeAPIError
from apps.marketplace.shopee.models import ShopeeSyncLog
from apps.marketplace.shopee.product_match import ShopeeProductMatchService
from apps.marketplace.shopee.product_push import ShopeeProductPushService
from apps.marketplace.shopee.stock_sync import ShopeeStockSyncService
from apps.marketplace.shopee.tests.factories import ShopeeShopFactory
from apps.marketplace.tests.factories import MarketplaceConnectionFactory, MarketplaceFactory
from apps.purchasing.models import PurchaseOrder
from apps.purchasing.services.purchasing_service import PurchaseOrderService
from apps.purchasing.tests.factories import PurchaseOrderDetailFactory, PurchaseOrderFactory
from apps.sales.models import SalesOrder
from core.factories import CompanyFactory, WarehouseFactory
from core.models import UserProfile


class ShopeeOrderSyncTest(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.marketplace = MarketplaceFactory()
        self.product = ProductFactory(company=self.company)
        self.variant = ProductVariantFactory(
            product=self.product,
            company=self.company,
            total_available_qty=100,
        )
        self.variant.refresh_from_db()  # Get trigger-generated sku_variant_code
        self.pvw = ProductVariantWarehouseFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=100,
        )
        self.cogs = ProductCogsFactory(
            product_variant=self.variant,
            warehouse=self.warehouse,
            company=self.company,
            original_qty=100,
            remaining_qty=100,
            cogs_amount=50000,
        )
        self.shop = ShopeeShopFactory(
            company=self.company,
            marketplace=self.marketplace,
            default_warehouse=self.warehouse,
        )

    @patch("apps.marketplace.shopee.order_sync.ShopeeClient")
    def test_order_upsert_creates_sales_order(self, MockClient):
        sku = self.variant.sku_variant_code
        mock_client = MockClient.return_value
        mock_client.get_order_detail.return_value = {
            "order_list": [
                {
                    "order_sn": "SH_ORDER_001",
                    "order_status": "READY_TO_SHIP",
                    "create_time": int(timezone.now().timestamp()),
                    "total_amount": 200000,
                    "actual_shipping_fee": 10000,
                    "shipping_carrier": "JNE",
                    "tracking_number": "TRK001",
                    "recipient_address": {
                        "name": "John Doe",
                        "phone": "08123456789",
                        "full_address": "Jl. Test 123",
                        "city": "Jakarta",
                        "state": "DKI Jakarta",
                    },
                    "item_list": [
                        {
                            "item_id": 12345,
                            "item_sku": sku,
                            "model_sku": sku,
                            "model_original_price": 100000,
                            "model_quantity_purchased": 2,
                        }
                    ],
                }
            ]
        }

        from apps.marketplace.shopee.order_sync import ShopeeOrderSyncer

        syncer = ShopeeOrderSyncer(self.shop)
        so = syncer.sync_order_by_sn("SH_ORDER_001")

        self.assertIsNotNone(so)
        self.assertEqual(so.marketplace_order_id, "SH_ORDER_001")
        self.assertEqual(so.customer_name, "John Doe")
        self.assertEqual(so.items.count(), 1)
        item = so.items.first()
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.selling_price, 100000)

    @patch("apps.marketplace.shopee.order_sync.ShopeeClient")
    def test_order_upsert_skips_duplicate(self, MockClient):
        sku = self.variant.sku_variant_code
        mock_client = MockClient.return_value
        mock_client.get_order_detail.return_value = {
            "order_list": [
                {
                    "order_sn": "SH_ORDER_DUP",
                    "order_status": "READY_TO_SHIP",
                    "create_time": int(timezone.now().timestamp()),
                    "total_amount": 100000,
                    "actual_shipping_fee": 0,
                    "shipping_carrier": "",
                    "tracking_number": "",
                    "recipient_address": {
                        "name": "Jane",
                        "phone": "081",
                        "full_address": "Addr",
                        "city": "City",
                        "state": "State",
                    },
                    "item_list": [
                        {
                            "item_id": 1,
                            "model_sku": sku,
                            "model_original_price": 100000,
                            "model_quantity_purchased": 1,
                        }
                    ],
                }
            ]
        }

        from apps.marketplace.shopee.order_sync import ShopeeOrderSyncer

        syncer = ShopeeOrderSyncer(self.shop)
        so1 = syncer.sync_order_by_sn("SH_ORDER_DUP")
        so2 = syncer.sync_order_by_sn("SH_ORDER_DUP")

        self.assertIsNotNone(so1)
        self.assertIsNotNone(so2)
        self.assertEqual(SalesOrder.objects.filter(marketplace_order_id="SH_ORDER_DUP").count(), 1)

    @patch("apps.marketplace.shopee.order_sync.ShopeeClient")
    def test_sync_recent_orders_paginates_and_upserts_orders(self, MockClient):
        sku = self.variant.sku_variant_code
        mock_client = MockClient.return_value
        # Page 1: more=True, 2 orders
        mock_client.get_order_list.side_effect = [
            {
                "order_list": [{"order_sn": "SH_PAGE1_A"}, {"order_sn": "SH_PAGE1_B"}],
                "more": True,
                "next_cursor": "cursor2",
            },
            {
                "order_list": [{"order_sn": "SH_PAGE2_C"}],
                "more": False,
            },
        ]

        def detail_side_effect(batch):
            orders = {
                "SH_PAGE1_A": {
                    "order_sn": "SH_PAGE1_A",
                    "order_status": "COMPLETED",
                    "create_time": 0,
                    "item_list": [
                        {
                            "item_id": 1,
                            "model_sku": sku,
                            "model_original_price": 100000,
                            "model_quantity_purchased": 1,
                        }
                    ],
                    "recipient_address": {
                        "name": "A",
                        "phone": "0",
                        "full_address": "A",
                        "city": "A",
                        "state": "A",
                    },
                },
                "SH_PAGE1_B": {
                    "order_sn": "SH_PAGE1_B",
                    "order_status": "COMPLETED",
                    "create_time": 0,
                    "item_list": [
                        {
                            "item_id": 2,
                            "model_sku": sku,
                            "model_original_price": 100000,
                            "model_quantity_purchased": 1,
                        }
                    ],
                    "recipient_address": {
                        "name": "B",
                        "phone": "0",
                        "full_address": "B",
                        "city": "B",
                        "state": "B",
                    },
                },
                "SH_PAGE2_C": {
                    "order_sn": "SH_PAGE2_C",
                    "order_status": "COMPLETED",
                    "create_time": 0,
                    "item_list": [
                        {
                            "item_id": 3,
                            "model_sku": sku,
                            "model_original_price": 100000,
                            "model_quantity_purchased": 1,
                        }
                    ],
                    "recipient_address": {
                        "name": "C",
                        "phone": "0",
                        "full_address": "C",
                        "city": "C",
                        "state": "C",
                    },
                },
            }
            return {"order_list": [orders[sn] for sn in batch if sn in orders]}

        mock_client.get_order_detail.side_effect = detail_side_effect

        from apps.marketplace.shopee.order_sync import ShopeeOrderSyncer

        syncer = ShopeeOrderSyncer(self.shop)
        count = syncer.sync_recent_orders()

        self.assertEqual(count, 3)
        mock_client.get_order_list.assert_called()
        self.assertEqual(mock_client.get_order_detail.call_count, 2)
        self.assertEqual(
            SalesOrder.objects.filter(
                marketplace_order_id__in=["SH_PAGE1_A", "SH_PAGE1_B", "SH_PAGE2_C"]
            ).count(),
            3,
        )

    @patch("apps.marketplace.shopee.order_sync.ShopeeClient")
    def test_sync_recent_orders_respects_hours_back_window(self, MockClient):
        mock_client = MockClient.return_value
        mock_client.get_order_list.return_value = {"order_list": [], "more": False}

        from apps.marketplace.shopee.order_sync import ShopeeOrderSyncer

        syncer = ShopeeOrderSyncer(self.shop)
        syncer.sync_recent_orders(hours_back=48)

        call_args = mock_client.get_order_list.call_args[1]
        time_diff = call_args["time_to"] - call_args["time_from"]
        # 48 hours = 172800 seconds
        self.assertAlmostEqual(time_diff, 172800, delta=10)

    @patch("apps.marketplace.shopee.order_sync.ShopeeClient")
    def test_sync_recent_orders_logs_and_stops_on_get_order_list_failure(self, MockClient):
        mock_client = MockClient.return_value
        mock_client.get_order_list.side_effect = ShopeeAPIError(500, "API_ERROR", "list failed")

        from apps.marketplace.shopee.order_sync import ShopeeOrderSyncer

        syncer = ShopeeOrderSyncer(self.shop)
        count = syncer.sync_recent_orders()

        self.assertEqual(count, 0)
        self.shop.refresh_from_db()
        self.assertIsNotNone(self.shop.last_order_sync_at)

    @patch("apps.marketplace.shopee.order_sync.ShopeeClient")
    def test_sync_recent_orders_continues_after_get_order_detail_failure(self, MockClient):
        mock_client = MockClient.return_value
        mock_client.get_order_list.return_value = {
            "order_list": [{"order_sn": "SH_DETAIL_FAIL"}],
            "more": False,
        }
        mock_client.get_order_detail.side_effect = ShopeeAPIError(500, "API_ERROR", "detail failed")

        from apps.marketplace.shopee.order_sync import ShopeeOrderSyncer

        syncer = ShopeeOrderSyncer(self.shop)
        count = syncer.sync_recent_orders()

        self.assertEqual(count, 0)
        self.shop.refresh_from_db()
        self.assertIsNotNone(self.shop.last_order_sync_at)


class TestShopeeOrderWebhook(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.marketplace = MarketplaceFactory()
        self.product = ProductFactory(company=self.company)
        self.variant = ProductVariantFactory(
            product=self.product,
            company=self.company,
        )
        self.variant.refresh_from_db()
        self.shop = ShopeeShopFactory(
            company=self.company,
            marketplace=self.marketplace,
            default_warehouse=self.warehouse,
        )

    def _order_detail_payload(self, sku: str, order_sn: str = "SH_WH_001") -> dict:
        return {
            "order_list": [
                {
                    "order_sn": order_sn,
                    "order_status": "READY_TO_SHIP",
                    "create_time": int(timezone.now().timestamp()),
                    "actual_shipping_fee": 10000,
                    "shipping_carrier": "JNE",
                    "tracking_number": "TRK001",
                    "recipient_address": {
                        "name": "John Doe",
                        "phone": "08123456789",
                        "full_address": "Jl. Test 123",
                        "city": "Jakarta",
                        "state": "DKI Jakarta",
                    },
                    "item_list": [
                        {
                            "item_id": 12345,
                            "model_sku": sku,
                            "model_original_price": 100000,
                            "model_quantity_purchased": 1,
                        }
                    ],
                }
            ]
        }

    @patch("apps.marketplace.shopee.order_sync.ShopeeClient")
    def test_webhook_end_to_end_creates_sales_order(self, MockClient):
        """POST webhook code 3 → order fetched from Shopee API → SalesOrder created in DB."""
        sku = self.variant.sku_variant_code
        MockClient.return_value.get_order_detail.return_value = self._order_detail_payload(sku)

        payload = json.dumps(
            {
                "code": 3,
                "shop_id": self.shop.shop_id,
                "ordersn": "SH_WH_001",
                "status": "READY_TO_SHIP",
            }
        )
        resp = self.client.post(
            "/shopee/webhook/",
            data=payload,
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(SalesOrder.objects.filter(marketplace_order_id="SH_WH_001").exists())

    @patch("apps.marketplace.shopee.order_sync.ShopeeClient")
    def test_order_upsert_source_platform_is_shopee(self, MockClient):
        """SalesOrder created from Shopee sync has source_platform=SHOPEE, not MANUAL."""
        sku = self.variant.sku_variant_code
        MockClient.return_value.get_order_detail.return_value = self._order_detail_payload(
            sku, "SH_PLAT_001"
        )

        from apps.marketplace.shopee.order_sync import ShopeeOrderSyncer

        syncer = ShopeeOrderSyncer(self.shop)
        so = syncer.sync_order_by_sn("SH_PLAT_001")

        self.assertIsNotNone(so)
        self.assertEqual(so.source_platform, SalesOrder.SourcePlatform.SHOPEE)

    def test_find_variant_restricted_to_company(self):
        """_find_variant returns None for a variant that belongs to a different company."""
        other_company = CompanyFactory()
        other_variant = ProductVariantFactory(company=other_company)
        other_variant.refresh_from_db()
        sku = other_variant.sku_variant_code

        from apps.marketplace.shopee.order_sync import ShopeeOrderSyncer

        syncer = ShopeeOrderSyncer(self.shop)  # self.shop belongs to self.company
        result = syncer._find_variant({"model_sku": sku})

        self.assertIsNone(result)

    @patch("apps.marketplace.shopee.order_sync.ShopeeClient")
    def test_order_upsert_skips_no_default_warehouse(self, MockClient):
        """sync_order_by_sn returns None when shop has no default_warehouse configured."""
        sku = self.variant.sku_variant_code
        shop_no_wh = ShopeeShopFactory(
            company=self.company,
            marketplace=self.marketplace,
            default_warehouse=None,
        )
        MockClient.return_value.get_order_detail.return_value = self._order_detail_payload(
            sku, "SH_NOWH_001"
        )

        from apps.marketplace.shopee.order_sync import ShopeeOrderSyncer

        syncer = ShopeeOrderSyncer(shop_no_wh)
        so = syncer.sync_order_by_sn("SH_NOWH_001")

        self.assertIsNone(so)
        self.assertFalse(SalesOrder.objects.filter(marketplace_order_id="SH_NOWH_001").exists())


class TestShopeeStockSyncService(TestCase):
    """ShopeeStockSyncService: stock push to Shopee."""

    def setUp(self):
        self.company = CompanyFactory()
        self.marketplace = MarketplaceFactory()
        self.shop = ShopeeShopFactory(
            company=self.company,
            marketplace=self.marketplace,
        )
        self.service = ShopeeStockSyncService()

    def _create_variant_with_stock(
        self,
        sku: str = "SKU-001",
        is_fake: bool = False,
        physical_qty: int = 10,
        checkout_qty: int = 0,
        warehouse_visible: bool = True,
        listing_active: bool = True,
        shopee_item_id: int | None = 100001,
        shopee_model_id: int | None = 200001,
    ) -> ProductVariant:
        warehouse = WarehouseFactory(
            company=self.company,
            is_marketplace_visible=warehouse_visible,
        )
        variant = ProductVariantFactory(
            company=self.company,
            sku_variant_code=sku,
            is_fake=is_fake,
        )
        ProductVariantWarehouseFactory(
            product_variant=variant,
            warehouse=warehouse,
            company=self.company,
            physical_qty=physical_qty,
            checkout_qty=checkout_qty,
        )
        ProductVariantMarketplaceFactory(
            product_variant=variant,
            marketplace=self.marketplace,
            company=self.company,
            selling_price=10000,
            is_active=listing_active,
            shopee_item_id=shopee_item_id,
            shopee_model_id=shopee_model_id,
        )
        return variant

    # ── sync_single_variant tests ─────────────────────────────────

    @patch.object(ShopeeClient, "update_stock", return_value={"success": True})
    def test_sync_single_variant_success(self, mock_update: MagicMock) -> None:
        variant = self._create_variant_with_stock(physical_qty=15, checkout_qty=3)
        result = self.service.sync_single_variant(
            str(variant.id),
            self.shop,
        )
        self.assertTrue(result)
        from apps.marketplace.shopee.models import ShopeeStockSyncLog

        log = ShopeeStockSyncLog.objects.filter(
            variant_id=str(variant.id),
        ).first()
        self.assertIsNotNone(log)
        self.assertTrue(log.success)
        mock_update.assert_called_once()

    @patch.object(
        ShopeeClient,
        "update_stock",
        side_effect=ShopeeAPIError(400, "API_ERROR", "stock update failed"),
    )
    def test_sync_single_variant_api_failure(self, mock_update: MagicMock) -> None:
        variant = self._create_variant_with_stock()
        result = self.service.sync_single_variant(
            str(variant.id),
            self.shop,
        )
        self.assertFalse(result)
        from apps.marketplace.shopee.models import ShopeeStockSyncLog

        log = ShopeeStockSyncLog.objects.filter(
            variant_id=str(variant.id),
        ).first()
        self.assertIsNotNone(log)
        self.assertFalse(log.success)
        self.assertIn("API_ERROR", log.error_message)

    def test_sync_single_variant_skips_fake_variant(self) -> None:
        variant = self._create_variant_with_stock(is_fake=True)
        with patch.object(ShopeeClient, "update_stock") as mock_update:
            result = self.service.sync_single_variant(
                str(variant.id),
                self.shop,
            )
        self.assertFalse(result)
        from apps.marketplace.shopee.models import ShopeeStockSyncLog

        self.assertEqual(
            ShopeeStockSyncLog.objects.filter(
                variant_id=str(variant.id),
            ).count(),
            0,
        )
        mock_update.assert_not_called()

    def test_sync_single_variant_skips_inactive_marketplace(self) -> None:
        variant = self._create_variant_with_stock(listing_active=False)
        with patch.object(ShopeeClient, "update_stock") as mock_update:
            result = self.service.sync_single_variant(
                str(variant.id),
                self.shop,
            )
        self.assertFalse(result)
        mock_update.assert_not_called()

    # ── sync_all_variants tests ───────────────────────────────────

    @patch.object(ShopeeClient, "update_stock", return_value={"success": True})
    def test_sync_all_variants_batches_correctly(self, mock_update: MagicMock) -> None:
        product = ProductFactory(company=self.company)
        warehouse = WarehouseFactory(
            company=self.company,
            is_marketplace_visible=True,
        )
        for i in range(110):
            variant = ProductVariantFactory(
                product=product,
                company=self.company,
                variant_values={"1": f"V{i:04d}"},
            )
            ProductVariantWarehouseFactory(
                product_variant=variant,
                warehouse=warehouse,
                company=self.company,
                physical_qty=5,
            )
            ProductVariantMarketplaceFactory(
                product_variant=variant,
                marketplace=self.marketplace,
                company=self.company,
                selling_price=10000,
                is_active=True,
                shopee_item_id=1000,
                shopee_model_id=200000 + i,
            )
        result = self.service.sync_all_variants(self.shop)
        self.assertEqual(result["success"], 110)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["errors"], [])
        self.assertEqual(mock_update.call_count, 3)

    @patch.object(
        ShopeeClient,
        "update_stock",
        side_effect=[
            {"success": True},
            ShopeeAPIError(400, "STOCK_FAIL", "failed"),
            {"success": True},
        ],
    )
    def test_sync_all_variants_partial_failure(self, mock_update: MagicMock) -> None:
        product = ProductFactory(company=self.company)
        warehouse = WarehouseFactory(
            company=self.company,
            is_marketplace_visible=True,
        )
        for i in range(3):
            variant = ProductVariantFactory(
                product=product,
                company=self.company,
                variant_values={"1": f"P{i:04d}"},
            )
            ProductVariantWarehouseFactory(
                product_variant=variant,
                warehouse=warehouse,
                company=self.company,
                physical_qty=5,
            )
            ProductVariantMarketplaceFactory(
                product_variant=variant,
                marketplace=self.marketplace,
                company=self.company,
                selling_price=10000,
                is_active=True,
                shopee_item_id=1001 + i,
                shopee_model_id=200000 + i,
            )
        result = self.service.sync_all_variants(self.shop)
        self.assertEqual(result["success"], 2)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(len(result["errors"]), 1)

    # ── _get_available_qty tests ──────────────────────────────────

    def test_get_available_qty_sums_visible_warehouses_only(self) -> None:
        variant = ProductVariantFactory(company=self.company)
        visible_wh = WarehouseFactory(
            company=self.company,
            is_marketplace_visible=True,
        )
        hidden_wh = WarehouseFactory(
            company=self.company,
            is_marketplace_visible=False,
        )
        ProductVariantWarehouseFactory(
            product_variant=variant,
            warehouse=visible_wh,
            company=self.company,
            physical_qty=20,
            checkout_qty=5,
        )
        ProductVariantWarehouseFactory(
            product_variant=variant,
            warehouse=hidden_wh,
            company=self.company,
            physical_qty=100,
            checkout_qty=10,
        )
        qty = self.service._get_available_qty(str(variant.id))
        self.assertEqual(qty, 15)

    def test_get_available_qty_never_negative(self) -> None:
        variant = ProductVariantFactory(company=self.company)
        wh = WarehouseFactory(
            company=self.company,
            is_marketplace_visible=True,
        )
        ProductVariantWarehouseFactory(
            product_variant=variant,
            warehouse=wh,
            company=self.company,
            physical_qty=3,
            checkout_qty=10,
        )
        qty = self.service._get_available_qty(str(variant.id))
        self.assertEqual(qty, 0)

    # ── _trigger_shopee_sync tests ────────────────────────────────

    @patch.object(
        ShopeeStockSyncService,
        "sync_single_variant",
        side_effect=Exception("Unexpected sync error"),
    )
    def test_trigger_shopee_sync_swallows_exception(
        self,
        mock_sync: MagicMock,
    ) -> None:
        variant = self._create_variant_with_stock()
        MarketplaceConnectionFactory(
            company=self.company,
            platform="SHOPEE",
            is_active=True,
            shopee_shop=self.shop,
        )
        service = InventoryService()
        try:
            service._trigger_shopee_sync(str(variant.id), str(self.company.id))
        except Exception:
            self.fail("_trigger_shopee_sync raised unexpectedly")
        mock_sync.assert_called_once()

    # ── sync_batch tests ────────────────────────────────────

    @patch.object(ShopeeClient, "update_stock", return_value={"success": True})
    def test_sync_batch_batches_correctly(self, mock_update: MagicMock) -> None:
        product = ProductFactory(company=self.company)
        warehouse = WarehouseFactory(company=self.company, is_marketplace_visible=True)
        variant_ids = []
        for i in range(51):
            variant = ProductVariantFactory(
                product=product,
                company=self.company,
                variant_values={"1": f"B{i:04d}"},
            )
            ProductVariantWarehouseFactory(
                product_variant=variant,
                warehouse=warehouse,
                company=self.company,
                physical_qty=5,
            )
            ProductVariantMarketplaceFactory(
                product_variant=variant,
                marketplace=self.marketplace,
                company=self.company,
                selling_price=10000,
                is_active=True,
                shopee_item_id=9999,
                shopee_model_id=300000 + i,
            )
            variant_ids.append(str(variant.id))
        result = self.service.sync_batch(variant_ids, self.shop)
        self.assertEqual(result["synced"], 51)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["failed_variant_ids"], [])
        self.assertEqual(mock_update.call_count, 2)

    def test_sync_batch_partial_failure(self, *args) -> None:
        product = ProductFactory(company=self.company)
        warehouse = WarehouseFactory(company=self.company, is_marketplace_visible=True)
        variant_ids = []
        for i in range(3):
            variant = ProductVariantFactory(
                product=product,
                company=self.company,
                variant_values={"1": f"PF{i:04d}"},
            )
            ProductVariantWarehouseFactory(
                product_variant=variant,
                warehouse=warehouse,
                company=self.company,
                physical_qty=5,
            )
            ProductVariantMarketplaceFactory(
                product_variant=variant,
                marketplace=self.marketplace,
                company=self.company,
                selling_price=10000,
                is_active=True,
                shopee_item_id=7000 + i,
                shopee_model_id=400000 + i,
            )
            variant_ids.append(str(variant.id))

        with patch.object(
            ShopeeClient,
            "update_stock",
            side_effect=[
                {"success": True},
                ShopeeAPIError(400, "STOCK_FAIL", "failed"),
                {"success": True},
            ],
        ):
            result = self.service.sync_batch(variant_ids, self.shop)

        self.assertEqual(result["synced"], 2)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(len(result["failed_variant_ids"]), 1)

    # ── post-hook tests ─────────────────────────────────────

    @patch.object(ShopeeStockSyncService, "sync_single_variant", return_value=True)
    def test_post_hook_fires_on_stock_movement(self, mock_sync: MagicMock) -> None:
        variant = self._create_variant_with_stock(physical_qty=20)
        warehouse = ProductVariantWarehouse.objects.get(product_variant=variant)
        MarketplaceConnectionFactory(
            company=self.company,
            platform="SHOPEE",
            is_active=True,
            shopee_shop=self.shop,
        )
        service = InventoryService()
        service.record_single_stock_movement(
            variant_id=variant.id,
            warehouse_id=warehouse.warehouse_id,
            qty=5,
            movement_type=StockMovement.MovementType.OUTBOUND,
        )
        mock_sync.assert_called_once_with(str(variant.id), self.shop)

    @patch.object(
        ShopeeStockSyncService,
        "sync_single_variant",
        side_effect=Exception("Catastrophic sync failure"),
    )
    def test_post_hook_isolation_on_sync_exception(self, mock_sync: MagicMock) -> None:
        from apps.inventory.models import StockMovement

        variant = self._create_variant_with_stock(physical_qty=20)
        warehouse = ProductVariantWarehouse.objects.get(product_variant=variant)
        MarketplaceConnectionFactory(
            company=self.company,
            platform="SHOPEE",
            is_active=True,
            shopee_shop=self.shop,
        )
        movement_count_before = StockMovement.objects.count()
        service = InventoryService()
        try:
            service.record_single_stock_movement(
                variant_id=variant.id,
                warehouse_id=warehouse.warehouse_id,
                qty=5,
                movement_type=StockMovement.MovementType.OUTBOUND,
            )
        except Exception:
            self.fail("record_single_stock_movement raised unexpectedly when sync failed")
        self.assertEqual(StockMovement.objects.count(), movement_count_before + 1)


class TestPODeliveredShopeeSync(TestCase):
    def test_po_delivered_triggers_sync_for_affected_variants(self):
        company = CompanyFactory()
        warehouse = WarehouseFactory(company=company)
        marketplace = MarketplaceFactory()
        shop = ShopeeShopFactory(company=company, marketplace=marketplace, is_active=True)
        MarketplaceConnectionFactory(
            company=company,
            platform="SHOPEE",
            is_active=True,
            shopee_shop=shop,
        )
        variant = ProductVariantFactory(company=company)
        ProductVariantMarketplaceFactory(
            product_variant=variant,
            marketplace=marketplace,
            company=company,
            is_active=True,
            shopee_item_id=111,
            shopee_model_id=222,
        )
        ProductVariantWarehouseFactory(
            product_variant=variant,
            warehouse=warehouse,
            company=company,
            physical_qty=10,
        )
        po = PurchaseOrderFactory(
            company=company,
            warehouse=warehouse,
            status=PurchaseOrder.POStatus.SHIPPED,
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=variant,
            company=company,
            ordered_qty=5,
        )

        with patch(
            "apps.marketplace.shopee.stock_sync.ShopeeStockSyncService.sync_batch",
            return_value={"synced": 1, "failed": 0, "failed_variant_ids": []},
        ) as mock_sync:
            with self.captureOnCommitCallbacks(execute=True):
                PurchaseOrderService().update_purchase_order(
                    po,
                    {
                        "status": PurchaseOrder.POStatus.DELIVERED,
                        "order_details": [
                            {
                                "id": str(detail.id),
                                "product_variant_id": str(variant.id),
                                "received_qty": 5,
                                "ordered_qty": 5,
                                "received_date": timezone.now().date().isoformat(),
                            }
                        ],
                    },
                )

        self.assertTrue(mock_sync.called)

    def test_po_delivered_sync_exception_does_not_rollback_po(self):
        company = CompanyFactory()
        warehouse = WarehouseFactory(company=company)
        marketplace = MarketplaceFactory()
        shop = ShopeeShopFactory(company=company, marketplace=marketplace, is_active=True)
        MarketplaceConnectionFactory(
            company=company,
            platform="SHOPEE",
            is_active=True,
            shopee_shop=shop,
        )
        variant = ProductVariantFactory(company=company)
        ProductVariantMarketplaceFactory(
            product_variant=variant,
            marketplace=marketplace,
            company=company,
            is_active=True,
            shopee_item_id=111,
            shopee_model_id=222,
        )
        ProductVariantWarehouseFactory(
            product_variant=variant,
            warehouse=warehouse,
            company=company,
            physical_qty=10,
        )
        po = PurchaseOrderFactory(
            company=company,
            warehouse=warehouse,
            status=PurchaseOrder.POStatus.SHIPPED,
        )
        detail = PurchaseOrderDetailFactory(
            purchase_order=po,
            product_variant=variant,
            company=company,
            ordered_qty=5,
        )

        with patch(
            "apps.marketplace.shopee.stock_sync.ShopeeStockSyncService.sync_batch",
            side_effect=Exception("network error"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                PurchaseOrderService().update_purchase_order(
                    po,
                    {
                        "status": PurchaseOrder.POStatus.DELIVERED,
                        "order_details": [
                            {
                                "id": str(detail.id),
                                "product_variant_id": str(variant.id),
                                "received_qty": 5,
                                "ordered_qty": 5,
                                "received_date": timezone.now().date().isoformat(),
                            }
                        ],
                    },
                )

        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.POStatus.DELIVERED)


class TestShopeeManagementCommand(TestCase):
    def test_push_command_finalizes_log_on_exception(self):
        company = CompanyFactory()
        marketplace = MarketplaceFactory()
        shop = ShopeeShopFactory(is_active=True, marketplace=marketplace)
        product = ProductFactory(company=company)
        variant = ProductVariantFactory(product=product, company=company)
        variant.refresh_from_db()
        ProductVariantMarketplaceFactory(
            product_variant=variant,
            marketplace=marketplace,
            company=company,
            is_active=True,
            shopee_item_id=None,
            shopee_model_id=None,
        )
        with patch(
            "apps.marketplace.shopee.product_push.ShopeeProductPushService.push_product",
            side_effect=Exception("crash"),
        ):
            ShopeeProductPushService().sync_all_active_shops_push()
        log = ShopeeSyncLog.objects.get(shop=shop, sync_type="product_push")
        self.assertEqual(log.status, "failed")
        self.assertIsNotNone(log.finished_at)
        self.assertIn("crash", log.error_message)

    def test_push_command_sets_failed_when_no_products_pushed(self):
        company = CompanyFactory()
        marketplace = MarketplaceFactory()
        shop = ShopeeShopFactory(is_active=True, marketplace=marketplace)
        category = Category.objects.create(
            company=company, name="Cat", category_code="C1", shopee_category_id=111
        )
        product = Product.objects.create(
            company=company, category=category, name="P1", weight=100, variant_options=[]
        )
        variant = ProductVariantFactory(product=product, company=company)
        ProductVariantMarketplaceFactory(
            product_variant=variant,
            marketplace=marketplace,
            company=company,
            is_active=True,
            shopee_item_id=None,
            shopee_model_id=None,
        )
        with patch(
            "apps.marketplace.shopee.product_push.ShopeeProductPushService.push_product",
            return_value={"item_id": None, "models_pushed": 0, "errors": ["API error"]},
        ):
            ShopeeProductPushService().sync_all_active_shops_push()
        log = ShopeeSyncLog.objects.get(shop=shop, sync_type="product_push")
        self.assertEqual(log.status, "failed")

    def test_update_command_finalizes_log_on_exception(self):
        company = CompanyFactory()
        marketplace = MarketplaceFactory()
        shop = ShopeeShopFactory(is_active=True, marketplace=marketplace)
        product = ProductFactory(company=company)
        variant = ProductVariantFactory(product=product, company=company)
        variant.refresh_from_db()
        ProductVariantMarketplaceFactory(
            product_variant=variant,
            marketplace=marketplace,
            company=company,
            is_active=True,
            shopee_item_id=999,
            shopee_model_id=0,
        )
        with patch(
            "apps.marketplace.shopee.product_push.ShopeeProductPushService.update_product",
            side_effect=Exception("crash"),
        ):
            ShopeeProductPushService().sync_all_active_shops_update_products()
        log = ShopeeSyncLog.objects.get(shop=shop, sync_type="product_update")
        self.assertEqual(log.status, "failed")
        self.assertIsNotNone(log.finished_at)
        self.assertIn("crash", log.error_message)

    def test_update_command_sets_failed_when_no_products_updated(self):
        company = CompanyFactory()
        marketplace = MarketplaceFactory()
        shop = ShopeeShopFactory(is_active=True, marketplace=marketplace)
        category = Category.objects.create(
            company=company, name="Cat", category_code="C2", shopee_category_id=222
        )
        product = Product.objects.create(
            company=company, category=category, name="P2", weight=100, variant_options=[]
        )
        variant = ProductVariantFactory(product=product, company=company)
        ProductVariantMarketplaceFactory(
            product_variant=variant,
            marketplace=marketplace,
            company=company,
            is_active=True,
            shopee_item_id=999,
            shopee_model_id=0,
        )
        with patch(
            "apps.marketplace.shopee.product_push.ShopeeProductPushService.update_product",
            return_value={"updated": False, "errors": ["API error"]},
        ):
            ShopeeProductPushService().sync_all_active_shops_update_products()
        log = ShopeeSyncLog.objects.get(shop=shop, sync_type="product_update")
        self.assertEqual(log.status, "failed")

    def test_command_creates_success_log(self):
        from apps.marketplace.shopee.stock_sync import ShopeeStockSyncService

        shop = ShopeeShopFactory(is_active=True)

        with patch(
            "apps.marketplace.shopee.stock_sync.ShopeeStockSyncService.sync_all_variants",
            return_value={"success": 5, "failed": 0, "errors": []},
        ):
            ShopeeStockSyncService().sync_all_active_shops_stock()

        self.assertTrue(
            ShopeeSyncLog.objects.filter(shop=shop, sync_type="stock", status="success").exists()
        )
        log = ShopeeSyncLog.objects.get(shop=shop)
        self.assertEqual(log.records_synced, 5)

    def test_command_logs_failure_on_exception(self):
        from apps.marketplace.shopee.stock_sync import ShopeeStockSyncService

        shop = ShopeeShopFactory(is_active=True)

        with patch(
            "apps.marketplace.shopee.stock_sync.ShopeeStockSyncService.sync_all_variants",
            side_effect=Exception("boom"),
        ):
            ShopeeStockSyncService().sync_all_active_shops_stock()

        log = ShopeeSyncLog.objects.get(shop=shop)
        self.assertEqual(log.status, "failed")
        self.assertIn("boom", log.error_message)


class TestShopeeSyncBatchFix(TestCase):
    def test_sync_batch_logs_exception_on_outer_error(self):
        company = CompanyFactory()
        marketplace = MarketplaceFactory()
        shop = ShopeeShopFactory(company=company, marketplace=marketplace, is_active=True)
        service = ShopeeStockSyncService()
        with patch(
            "apps.catalog.models.ProductVariantMarketplace.objects.filter",
            side_effect=Exception("db down"),
        ):
            with self.assertLogs("apps.marketplace.shopee.stock_sync", level="ERROR") as cm:
                result = service.sync_batch(["fake-id"], shop)
        self.assertEqual(result["failed"], 1)
        self.assertTrue(any("sync_batch failed" in msg for msg in cm.output))

    def test_sync_all_variants_catches_network_error(self):
        company = CompanyFactory()
        marketplace = MarketplaceFactory()
        shop = ShopeeShopFactory(company=company, marketplace=marketplace, is_active=True)
        variant = ProductVariantFactory(company=company)
        variant.refresh_from_db()
        ProductVariantMarketplaceFactory(
            product_variant=variant,
            marketplace=marketplace,
            company=company,
            is_active=True,
            shopee_item_id=111,
            shopee_model_id=222,
        )
        ProductVariantWarehouseFactory(
            product_variant=variant,
            warehouse=WarehouseFactory(company=company),
            company=company,
            physical_qty=10,
        )
        with patch(
            "apps.marketplace.shopee.client.ShopeeClient.update_stock",
            side_effect=Exception("ConnectionError: timeout"),
        ):
            result = ShopeeStockSyncService().sync_all_variants(shop)
        self.assertGreater(result["failed"], 0)
        self.assertEqual(result["success"], 0)


class TestShopeeProductMatch(TestCase):
    def test_match_writes_shopee_ids_for_multivariant_item(self):
        company = CompanyFactory()
        marketplace = MarketplaceFactory()
        shop = ShopeeShopFactory(is_active=True, marketplace=marketplace)
        MarketplaceConnectionFactory(
            company=company, shopee_shop=shop, is_active=True, platform="SHOPEE"
        )

        # DB trigger auto-generates sku_variant_code from Product.sku_code + variant_values.
        # We set variant_values={'1': 'RED'} so the generated sku_variant_code is parent_sku + '-RED'.
        product = ProductFactory(company=company)
        variant = ProductVariantFactory(
            product=product, company=company, variant_values={"1": "RED"}
        )
        variant.refresh_from_db()  # DB trigger sets sku_variant_code
        model_sku = variant.sku_variant_code  # e.g. "CAT-0000-001-RED"

        pvm = ProductVariantMarketplaceFactory(
            product_variant=variant,
            marketplace=marketplace,
            company=company,
            is_active=True,
            shopee_item_id=None,
            shopee_model_id=None,
        )

        with (
            patch(
                "apps.marketplace.shopee.client.ShopeeClient.get_item_list",
                return_value={"item_id_list": [111], "has_next_page": False},
            ),
            patch(
                "apps.marketplace.shopee.client.ShopeeClient.get_item_base_info",
                return_value={"item_list": [{"item_id": 111, "item_sku": "SKU001"}]},
            ),
            patch(
                "apps.marketplace.shopee.client.ShopeeClient.get_model_list",
                return_value={"model": [{"model_id": 222, "model_sku": model_sku}]},
            ),
        ):
            result = ShopeeProductMatchService().match_products_for_shop(shop)

        pvm.refresh_from_db()
        self.assertEqual(pvm.shopee_item_id, 111)
        self.assertEqual(pvm.shopee_model_id, 222)
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["errors"], [])

    def test_match_skips_when_no_variant_found(self):
        company = CompanyFactory()
        marketplace = MarketplaceFactory()
        shop = ShopeeShopFactory(is_active=True, marketplace=marketplace)
        MarketplaceConnectionFactory(
            company=company, shopee_shop=shop, is_active=True, platform="SHOPEE"
        )

        product = ProductFactory(company=company)
        variant = ProductVariantFactory(
            product=product, company=company, variant_values={"1": "BLUE"}
        )

        pvm = ProductVariantMarketplaceFactory(
            product_variant=variant,
            marketplace=marketplace,
            company=company,
            shopee_item_id=None,
            shopee_model_id=None,
        )

        with (
            patch(
                "apps.marketplace.shopee.client.ShopeeClient.get_item_list",
                return_value={"item_id_list": [111], "has_next_page": False},
            ),
            patch(
                "apps.marketplace.shopee.client.ShopeeClient.get_item_base_info",
                return_value={"item_list": [{"item_id": 111, "item_sku": "SKU001"}]},
            ),
            patch(
                "apps.marketplace.shopee.client.ShopeeClient.get_model_list",
                return_value={"model": [{"model_id": 222, "model_sku": "NONEXISTENT-SKU"}]},
            ),
        ):
            result = ShopeeProductMatchService().match_products_for_shop(shop)

        pvm.refresh_from_db()
        self.assertIsNone(pvm.shopee_item_id)
        self.assertGreaterEqual(result["skipped"], 1)

    def test_match_restricts_update_to_correct_company(self):
        company_a = CompanyFactory()
        company_b = CompanyFactory()
        marketplace = MarketplaceFactory()
        shop = ShopeeShopFactory(is_active=True, marketplace=marketplace)
        MarketplaceConnectionFactory(
            company=company_a, shopee_shop=shop, is_active=True, platform="SHOPEE"
        )

        product_a = ProductFactory(company=company_a)
        variant_a = ProductVariantFactory(
            product=product_a, company=company_a, variant_values={"1": "RED"}
        )
        variant_a.refresh_from_db()
        model_sku = variant_a.sku_variant_code

        product_b = ProductFactory(company=company_b)
        variant_b = ProductVariantFactory(product=product_b, company=company_b)

        pvm_a = ProductVariantMarketplaceFactory(
            product_variant=variant_a,
            marketplace=marketplace,
            company=company_a,
            is_active=True,
            shopee_item_id=None,
            shopee_model_id=None,
        )
        pvm_b = ProductVariantMarketplaceFactory(
            product_variant=variant_b,
            marketplace=marketplace,
            company=company_b,
            is_active=True,
            shopee_item_id=None,
            shopee_model_id=None,
        )

        with (
            patch(
                "apps.marketplace.shopee.client.ShopeeClient.get_item_list",
                return_value={"item_id_list": [111], "has_next_page": False},
            ),
            patch(
                "apps.marketplace.shopee.client.ShopeeClient.get_item_base_info",
                return_value={"item_list": [{"item_id": 111, "item_sku": model_sku}]},
            ),
            patch(
                "apps.marketplace.shopee.client.ShopeeClient.get_model_list",
                return_value={"model": [{"model_id": 222, "model_sku": model_sku}]},
            ),
        ):
            ShopeeProductMatchService().match_products_for_shop(shop)

        pvm_a.refresh_from_db()
        pvm_b.refresh_from_db()
        self.assertEqual(pvm_a.shopee_item_id, 111)
        self.assertIsNone(pvm_b.shopee_item_id)

    def test_match_command_creates_success_log(self):
        shop = ShopeeShopFactory(is_active=True)

        with patch(
            "apps.marketplace.shopee.product_match.ShopeeProductMatchService.match_products_for_shop",
            return_value={"matched": 3, "skipped": 1, "errors": []},
        ):
            ShopeeProductMatchService().sync_all_active_shops_product_match()

        log = ShopeeSyncLog.objects.get(shop=shop, sync_type="product_match")
        self.assertEqual(log.status, "success")
        self.assertEqual(log.records_synced, 3)


class TestShopeeProductPush(TestCase):
    """ShopeeProductPushService: push products to Shopee."""

    def test_push_single_variant_product_writes_item_id(self):
        company = CompanyFactory()
        marketplace = MarketplaceFactory()
        shop = ShopeeShopFactory(is_active=True, marketplace=marketplace)
        MarketplaceConnectionFactory(
            company=company, shopee_shop=shop, is_active=True, platform="SHOPEE"
        )
        category = Category.objects.create(
            company=company, name="Test Cat", category_code="TC", shopee_category_id=12345
        )
        product = Product.objects.create(
            company=company, category=category, name="Test Product", weight=500, variant_options=[]
        )
        variant = ProductVariantFactory(product=product, company=company)
        variant.refresh_from_db()
        pvm = ProductVariantMarketplaceFactory(
            product_variant=variant,
            marketplace=marketplace,
            company=company,
            selling_price=50000,
            shopee_item_id=None,
            shopee_model_id=None,
        )

        with (
            patch(
                "apps.marketplace.shopee.client.ShopeeClient.get_channel_list",
                return_value={
                    "logistics_channel_list": [{"logistics_channel_id": 1, "enabled": True}]
                },
            ),
            patch("apps.marketplace.shopee.client.ShopeeClient.upload_image", return_value=""),
            patch(
                "apps.marketplace.shopee.client.ShopeeClient.add_item",
                return_value={"item_id": 999},
            ),
        ):
            result = ShopeeProductPushService().push_product(product, shop)

        self.assertEqual(result["item_id"], 999)
        pvm.refresh_from_db()
        self.assertEqual(pvm.shopee_item_id, 999)
        self.assertEqual(pvm.shopee_model_id, 0)

    def test_push_skips_product_without_shopee_category_id(self):
        company = CompanyFactory()
        marketplace = MarketplaceFactory()
        shop = ShopeeShopFactory(is_active=True, marketplace=marketplace)
        category = Category.objects.create(
            company=company, name="No Shopee Cat", category_code="NSC", shopee_category_id=None
        )
        product = Product.objects.create(
            company=company, category=category, name="No Cat Product", weight=500
        )
        variant = ProductVariantFactory(product=product, company=company)
        ProductVariantMarketplaceFactory(
            product_variant=variant,
            marketplace=marketplace,
            company=company,
            selling_price=25000,
            shopee_item_id=None,
            shopee_model_id=None,
        )

        result = ShopeeProductPushService().push_product(product, shop)

        self.assertIsNone(result["item_id"])
        self.assertTrue(len(result["errors"]) > 0)
        self.assertIn("shopee_category_id", result["errors"][0])

    def test_push_command_creates_success_log(self):
        shop = ShopeeShopFactory(is_active=True, marketplace=MarketplaceFactory())

        with patch(
            "apps.marketplace.shopee.product_push.ShopeeProductPushService.push_product",
            return_value={"item_id": 100, "models_pushed": 1, "errors": []},
        ):
            ShopeeProductPushService().sync_all_active_shops_push()

        log = ShopeeSyncLog.objects.filter(shop=shop, sync_type="product_push").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status, "success")


class TestShopeeProductUpdate(TestCase):
    def test_update_product_calls_update_item(self):
        company = CompanyFactory()
        marketplace = MarketplaceFactory()
        shop = ShopeeShopFactory(is_active=True, marketplace=marketplace)
        MarketplaceConnectionFactory(
            company=company, shopee_shop=shop, is_active=True, platform="SHOPEE"
        )
        category = Category.objects.create(
            company=company, name="Cat", category_code="C1", shopee_category_id=111
        )
        product = Product.objects.create(
            company=company, category=category, name="Old Name", weight=500, variant_options=[]
        )
        variant = ProductVariantFactory(product=product, company=company)
        variant.refresh_from_db()
        ProductVariantMarketplaceFactory(
            product_variant=variant,
            marketplace=marketplace,
            company=company,
            selling_price=50000,
            shopee_item_id=999,
            shopee_model_id=0,
        )

        with (
            patch(
                "apps.marketplace.shopee.client.ShopeeClient.upload_image",
                return_value="",
            ),
            patch(
                "apps.marketplace.shopee.client.ShopeeClient.update_item",
                return_value={"item_id": 999},
            ) as mock_update,
        ):
            product.name = "New Name"
            product.save(update_fields=["name"])
            result = ShopeeProductPushService().update_product(product, shop)

        self.assertTrue(result["updated"])
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        self.assertEqual(call_args[0][0], 999)
        self.assertEqual(call_args[0][1]["item_name"], "New Name")

    def test_update_product_skips_when_no_shopee_item_id(self):
        company = CompanyFactory()
        marketplace = MarketplaceFactory()
        shop = ShopeeShopFactory(is_active=True, marketplace=marketplace)
        category = Category.objects.create(
            company=company, name="Cat", category_code="C1", shopee_category_id=111
        )
        product = Product.objects.create(
            company=company, category=category, name="Test", weight=500, variant_options=[]
        )
        variant = ProductVariantFactory(product=product, company=company)
        variant.refresh_from_db()
        ProductVariantMarketplaceFactory(
            product_variant=variant,
            marketplace=marketplace,
            company=company,
            selling_price=50000,
            shopee_item_id=None,
            shopee_model_id=None,
        )

        result = ShopeeProductPushService().update_product(product, shop)

        self.assertFalse(result["updated"])
        self.assertTrue(len(result["errors"]) > 0)

    def test_product_update_view_triggers_shopee_sync(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        User = get_user_model()
        company = CompanyFactory()
        marketplace = MarketplaceFactory()
        shop = ShopeeShopFactory(is_active=True, marketplace=marketplace)
        MarketplaceConnectionFactory(
            company=company, shopee_shop=shop, is_active=True, platform="SHOPEE"
        )
        category = Category.objects.create(
            company=company, name="Cat", category_code="C3", shopee_category_id=111
        )
        product = Product.objects.create(
            company=company, category=category, name="Old Name", weight=500, variant_options=[]
        )

        user = User.objects.create_user(username="tester", password="pass", is_staff=True)
        UserProfile.objects.create(user=user, company=company, role="admin")
        client = APIClient()
        client.force_authenticate(user=user)

        with patch(
            "apps.catalog.services.product_service.ProductService._trigger_shopee_product_update"
        ) as mock_trigger:
            with self.captureOnCommitCallbacks(execute=True):
                client.patch(
                    f"/product/{product.id}/",
                    {"name": "New Name"},
                    format="json",
                )

        mock_trigger.assert_called_once_with(str(product.id))

    def test_update_command_creates_success_log(self):
        shop = ShopeeShopFactory(is_active=True, marketplace=MarketplaceFactory())

        with patch(
            "apps.marketplace.shopee.product_push.ShopeeProductPushService.update_product",
            return_value={"updated": True, "errors": []},
        ):
            ShopeeProductPushService().sync_all_active_shops_update_products()

        log = ShopeeSyncLog.objects.filter(shop=shop, sync_type="product_update").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status, "success")


class TestShopeeManageOrder(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.marketplace = MarketplaceFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.shop = ShopeeShopFactory(
            company=self.company,
            marketplace=self.marketplace,
            default_warehouse=self.warehouse,
        )

    @patch(
        "apps.marketplace.shopee.client.ShopeeClient.get_shipping_document_result",
        return_value={"file_url": "https://cdn.shopee.example.com/label.pdf", "result_list": []},
    )
    def test_shipping_document_action_returns_file_url(self, mock_get):
        """POST /shopee/shops/{id}/shipping-document/ returns file_url from Shopee API."""
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="labeluser", password="pass", is_staff=True)
        client = APIClient()
        client.force_authenticate(user=user)

        resp = client.post(
            f"/shopee/shops/{self.shop.id}/shipping-document/",
            {"order_sns": ["ORDER_001", "ORDER_002"]},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn("file_url", resp.data)
        self.assertEqual(resp.data["file_url"], "https://cdn.shopee.example.com/label.pdf")
        mock_get.assert_called_once_with(["ORDER_001", "ORDER_002"])

    def test_shipping_document_action_rejects_empty_order_sns(self):
        """POST with empty order_sns list returns 400."""
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="labeluser2", password="pass", is_staff=True)
        client = APIClient()
        client.force_authenticate(user=user)

        resp = client.post(
            f"/shopee/shops/{self.shop.id}/shipping-document/",
            {"order_sns": []},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_sales_order_list_filterable_by_source_platform(self):
        """GET /sales-orders/?source_platform=SHOPEE returns only Shopee-sourced orders."""
        SalesOrder.objects.create(
            company=self.company,
            warehouse=self.warehouse,
            order_date=timezone.now(),
            status=SalesOrder.OrderStatus.CONFIRMED,
            source_platform=SalesOrder.SourcePlatform.SHOPEE,
            marketplace_order_id="SH-MANAGE-001",
        )
        SalesOrder.objects.create(
            company=self.company,
            warehouse=self.warehouse,
            order_date=timezone.now(),
            status=SalesOrder.OrderStatus.CONFIRMED,
            source_platform=SalesOrder.SourcePlatform.MANUAL,
            marketplace_order_id="MANUAL-001",
        )

        from django.contrib.auth import get_user_model

        User = get_user_model()
        list_user = User.objects.create_user(username="listuser", password="pass")
        UserProfile.objects.create(user=list_user, company=self.company, role="viewer")

        client = APIClient()
        client.force_authenticate(user=list_user)
        resp = client.get("/sales-orders/?source_platform=SHOPEE")

        self.assertEqual(resp.status_code, 200)
        results = resp.data.get("results", resp.data)
        order_ids = [o["marketplace_order_id"] for o in results]
        self.assertIn("SH-MANAGE-001", order_ids)
        self.assertNotIn("MANUAL-001", order_ids)


class TestShopeePriceSync(TestCase):
    def test_update_price_for_listing_calls_update_price_api(self):
        company = CompanyFactory()
        marketplace = MarketplaceFactory()
        shop = ShopeeShopFactory(is_active=True, marketplace=marketplace)
        MarketplaceConnectionFactory(
            company=company, shopee_shop=shop, is_active=True, platform="SHOPEE"
        )
        variant = ProductVariantFactory(company=company)
        listing = ProductVariantMarketplaceFactory(
            product_variant=variant,
            marketplace=marketplace,
            company=company,
            is_active=True,
            selling_price=50000,
            shopee_item_id=111,
            shopee_model_id=222,
        )

        with patch(
            "apps.marketplace.shopee.client.ShopeeClient.update_price",
            return_value={},
        ) as mock_update:
            result = ShopeeProductPushService().update_price_for_listing(listing, shop)

        self.assertTrue(result["updated"])
        self.assertEqual(result["errors"], [])
        mock_update.assert_called_once_with(
            111,
            [{"model_id": 222, "original_price": 50000}],
        )

    def test_update_price_skips_when_no_shopee_item_id(self):
        company = CompanyFactory()
        marketplace = MarketplaceFactory()
        shop = ShopeeShopFactory(is_active=True, marketplace=marketplace)
        variant = ProductVariantFactory(company=company)
        listing = ProductVariantMarketplaceFactory(
            product_variant=variant,
            marketplace=marketplace,
            company=company,
            is_active=True,
            selling_price=50000,
            shopee_item_id=None,
            shopee_model_id=None,
        )

        result = ShopeeProductPushService().update_price_for_listing(listing, shop)

        self.assertFalse(result["updated"])
        self.assertTrue(len(result["errors"]) > 0)

    def test_update_prices_command_creates_success_log(self):
        company = CompanyFactory()
        marketplace = MarketplaceFactory()
        shop = ShopeeShopFactory(is_active=True, marketplace=marketplace)
        variant = ProductVariantFactory(company=company)
        ProductVariantMarketplaceFactory(
            product_variant=variant,
            marketplace=marketplace,
            company=company,
            is_active=True,
            selling_price=50000,
            shopee_item_id=111,
            shopee_model_id=222,
        )

        with patch(
            "apps.marketplace.shopee.product_push.ShopeeProductPushService.update_price_for_listing",
            return_value={"updated": True, "errors": []},
        ):
            ShopeeProductPushService().sync_all_active_shops_update_prices()

        log = ShopeeSyncLog.objects.get(shop=shop, sync_type="product_price")
        self.assertEqual(log.status, "success")
        self.assertEqual(log.records_synced, 1)
        self.assertIsNotNone(log.finished_at)

    def test_update_prices_view_action_triggers_shopee_sync(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        User = get_user_model()
        company = CompanyFactory()
        marketplace = MarketplaceFactory()
        shop = ShopeeShopFactory(is_active=True, marketplace=marketplace)
        MarketplaceConnectionFactory(
            company=company, shopee_shop=shop, is_active=True, platform="SHOPEE"
        )
        category = Category.objects.create(
            company=company, name="PriceCat", category_code="PC1", shopee_category_id=111
        )
        product = Product.objects.create(
            company=company, category=category, name="PriceProduct", weight=100, variant_options=[]
        )
        variant = ProductVariantFactory(product=product, company=company)
        variant.refresh_from_db()
        listing = ProductVariantMarketplaceFactory(
            product_variant=variant,
            marketplace=marketplace,
            company=company,
            is_active=True,
            selling_price=50000,
            shopee_item_id=111,
            shopee_model_id=222,
        )

        user = User.objects.create_user(username="priceuser", password="pass", is_staff=True)
        UserProfile.objects.create(user=user, company=company, role="admin")
        client = APIClient()
        client.force_authenticate(user=user)

        with patch(
            "apps.catalog.services.product_service.ProductService._trigger_shopee_price_update"
        ) as mock_trigger:
            with self.captureOnCommitCallbacks(execute=True):
                resp = client.patch(
                    f"/product/{product.id}/update_prices/",
                    [
                        {
                            "variant_id": str(variant.id),
                            "marketplace_id": str(marketplace.id),
                            "selling_price": 45000,
                        }
                    ],
                    format="json",
                )

        self.assertEqual(resp.status_code, 200)
        listing.refresh_from_db()
        self.assertEqual(listing.selling_price, 45000)
        mock_trigger.assert_called_once()
        call_args = mock_trigger.call_args[0]
        self.assertIn(str(listing.id), call_args[0])
        self.assertEqual(call_args[1], str(company.id))


class TestShopeeSyncOrdersService(TestCase):
    def test_sync_all_active_shops_orders_creates_sync_log(self):
        from apps.marketplace.shopee.order_sync import sync_all_active_shops_orders

        company = CompanyFactory()
        marketplace = MarketplaceFactory()
        warehouse = WarehouseFactory(company=company)
        shop = ShopeeShopFactory(
            is_active=True,
            company=company,
            marketplace=marketplace,
            default_warehouse=warehouse,
        )

        with patch(
            "apps.marketplace.shopee.order_sync.ShopeeOrderSyncer.sync_recent_orders",
            return_value=3,
        ):
            sync_all_active_shops_orders()

        log = ShopeeSyncLog.objects.get(shop=shop, sync_type="orders")
        self.assertEqual(log.status, "success")
        self.assertEqual(log.records_synced, 3)

    def test_sync_all_active_shops_orders_filters_by_shop_id(self):
        from apps.marketplace.shopee.order_sync import sync_all_active_shops_orders

        company = CompanyFactory()
        marketplace = MarketplaceFactory()
        warehouse = WarehouseFactory(company=company)
        shop1 = ShopeeShopFactory(
            is_active=True,
            company=company,
            marketplace=marketplace,
            default_warehouse=warehouse,
        )
        ShopeeShopFactory(
            is_active=True,
            company=company,
            marketplace=marketplace,
            default_warehouse=warehouse,
        )

        with patch(
            "apps.marketplace.shopee.order_sync.ShopeeOrderSyncer.sync_recent_orders",
            return_value=1,
        ):
            sync_all_active_shops_orders(shop_id=shop1.shop_id)

        logs = ShopeeSyncLog.objects.filter(sync_type="orders")
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.first().shop, shop1)

    def test_sync_all_active_shops_orders_logs_failure_on_exception(self):
        from apps.marketplace.shopee.order_sync import sync_all_active_shops_orders

        company = CompanyFactory()
        marketplace = MarketplaceFactory()
        warehouse = WarehouseFactory(company=company)
        shop = ShopeeShopFactory(
            is_active=True,
            company=company,
            marketplace=marketplace,
            default_warehouse=warehouse,
        )

        with patch(
            "apps.marketplace.shopee.order_sync.ShopeeOrderSyncer.sync_recent_orders",
            side_effect=Exception("oops"),
        ):
            sync_all_active_shops_orders()

        log = ShopeeSyncLog.objects.get(shop=shop, sync_type="orders")
        self.assertEqual(log.status, "failed")
        self.assertEqual(log.error_message, "oops")
