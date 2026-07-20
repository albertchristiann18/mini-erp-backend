from django.test import TestCase

from apps.catalog.tests.factories import ProductVariantFactory
from apps.marketplace.shopee.models import ShopeeStockSyncLog
from apps.marketplace.shopee.tests.factories import ShopeeShopFactory, ShopeeStockSyncLogFactory
from core.factories import CompanyFactory


class TestShopeeStockSyncLogModel(TestCase):
    """ShopeeStockSyncLog model creation and constraints."""

    def test_create_success_log(self) -> None:
        log = ShopeeStockSyncLogFactory(success=True)
        self.assertIsNotNone(log.id)
        self.assertIsNotNone(log.cdate)
        self.assertIn("OK", str(log))

    def test_create_failure_log(self) -> None:
        log = ShopeeStockSyncLogFactory(
            success=False,
            error_message="Connection timeout",
        )
        self.assertIn("FAIL", str(log))
        self.assertEqual(log.error_message, "Connection timeout")

    def test_variant_set_null_on_delete(self) -> None:
        log = ShopeeStockSyncLogFactory(variant__sku_variant_code="DELETE-ME")
        log.variant.delete()
        log.refresh_from_db()
        self.assertIsNone(log.variant_id)
        self.assertIsNotNone(log.sku_variant_code)
        self.assertEqual(ShopeeStockSyncLog.objects.count(), 1)

    def test_sku_snapshot_independent_of_variant(self) -> None:
        log = ShopeeStockSyncLogFactory(sku_variant_code="SKU-001")
        self.assertEqual(log.sku_variant_code, "SKU-001")
        log.variant.delete()
        log.refresh_from_db()
        self.assertEqual(log.sku_variant_code, "SKU-001")

    def test_ordering_newest_first(self) -> None:
        company = CompanyFactory()
        shop = ShopeeShopFactory(company=company)
        variant = ProductVariantFactory(company=company)
        log_a = ShopeeStockSyncLogFactory(
            company=company,
            shop=shop,
            variant=variant,
        )
        log_b = ShopeeStockSyncLogFactory(
            company=company,
            shop=shop,
            variant=variant,
        )
        qs = ShopeeStockSyncLog.objects.all()
        self.assertEqual(list(qs), [log_b, log_a])

    def test_sync_type_choices(self) -> None:
        company = CompanyFactory()
        shop = ShopeeShopFactory(company=company)
        variant = ProductVariantFactory(company=company)
        ShopeeStockSyncLogFactory(
            company=company,
            shop=shop,
            variant=variant,
            sync_type=ShopeeStockSyncLog.SyncType.FULL,
        )
        ShopeeStockSyncLogFactory(
            company=company,
            shop=shop,
            variant=variant,
            sync_type=ShopeeStockSyncLog.SyncType.SINGLE,
        )
        self.assertEqual(
            ShopeeStockSyncLog.objects.filter(sync_type=ShopeeStockSyncLog.SyncType.FULL).count(),
            1,
        )
        self.assertEqual(
            ShopeeStockSyncLog.objects.filter(
                sync_type=ShopeeStockSyncLog.SyncType.SINGLE,
            ).count(),
            1,
        )
