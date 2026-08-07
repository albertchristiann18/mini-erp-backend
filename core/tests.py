import io
from decimal import Decimal
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import serializers

from apps.catalog.tests.factories import ProductFactory, ProductVariantFactory
from apps.inventory.tests.factories import ProductCogsFactory, WarehouseFactory
from apps.purchasing.models import PurchaseOrder
from apps.purchasing.tests.factories import (
    ProductSupplierFactory,
    PurchaseOrderDetailFactory,
    PurchaseOrderFactory,
    SupplierFactory,
)
from core.factories import CompanyFactory, UserProfileFactory
from core.serializers import RoundedMoneyField
from core.services.migrate_service import (
    _parse_po_tracker,
    step_enrich_po_tracker,
    step_product_links,
    step_reconciliation,
    step_split_deliveries,
)
from core.services.user_service import create_user as run
from core.utils import round_money_to_int

User = get_user_model()


def _make_tracker_wb(rows_by_no: dict) -> MagicMock:
    """Build a mock workbook for _parse_po_tracker / step_enrich_po_tracker tests.

    rows_by_no maps tracker "no" integer to a list of raw row tuples.
    All rows are assembled in the order: 7 header rows then the data rows.
    """
    header_rows = [None] * 8  # rows[0..7] are header/ignored
    data_rows: list = []
    for no, rows in rows_by_no.items():
        for row in rows:
            # Ensure col 0 = no
            full_row = list(row)
            full_row[0] = no
            data_rows.append(tuple(full_row))

    all_rows = header_rows + data_rows

    ws = MagicMock()
    ws.iter_rows.return_value = iter(all_rows)

    wb = MagicMock()
    wb.__getitem__ = MagicMock(return_value=ws)
    return wb


def _make_split_detail_wb(sheet_name: str, detail_rows: list) -> MagicMock:
    """Build a mock PO detail workbook for split-delivery tests."""
    # rows[0..1] are header; data starts at [2]
    header_rows = [None, None]
    all_rows = header_rows + detail_rows

    ws = MagicMock()
    ws.iter_rows.return_value = iter(all_rows)

    # Also need "Master DATA All Item" sheet (not used in split test)
    empty_ws = MagicMock()
    empty_ws.iter_rows.return_value = iter([])

    def _getitem(key: str) -> MagicMock:
        if key == sheet_name:
            return ws
        return empty_ws

    wb = MagicMock()
    wb.__getitem__ = MagicMock(side_effect=_getitem)
    return wb


class CreateUserScriptTest(TestCase):
    def test_create_user_new_user_creates_company_and_profile(self):
        run(username="newuser")
        user = User.objects.get(username="newuser")
        self.assertTrue(user.has_usable_password())
        self.assertTrue(user.is_staff)
        self.assertEqual(user.profile.role, "admin")
        self.assertEqual(user.profile.company.name, "Newuser")

    def test_create_user_explicit_role_sets_is_staff_false(self):
        run(username="csuser", role="cs")
        user = User.objects.get(username="csuser")
        self.assertFalse(user.is_staff)
        self.assertEqual(user.profile.role, "cs")

    def test_create_user_explicit_password_is_used(self):
        run(username="pwuser", password="MySecretPw123")
        user = User.objects.get(username="pwuser")
        self.assertTrue(user.check_password("MySecretPw123"))

    def test_create_user_explicit_company_name(self):
        run(username="anyuser", company="Acme Corp")
        user = User.objects.get(username="anyuser")
        self.assertEqual(user.profile.company.name, "Acme Corp")

    def test_create_user_existing_username_is_idempotent(self):
        user = User.objects.create_user(username="existinguser", password="original_pw")
        UserProfileFactory(user=user, role="admin")
        run(username="existinguser")
        self.assertEqual(User.objects.filter(username="existinguser").count(), 1)
        self.assertEqual(User.objects.get(username="existinguser").profile.role, "admin")
        self.assertTrue(User.objects.get(username="existinguser").check_password("original_pw"))

    def test_create_user_existing_user_company_mismatch_gets_synced(self):
        old_company = CompanyFactory(name="OldCo")
        profile = UserProfileFactory(user__username="mismatcheduser", company=old_company)
        run(username="mismatcheduser", company="NewCo")
        profile.refresh_from_db()
        self.assertEqual(profile.company.name, "NewCo")

    def test_create_user_default_company_name_is_capitalized_username(self):
        run(username="teststaff")
        user = User.objects.get(username="teststaff")
        self.assertEqual(user.profile.company.name, "Teststaff")

    def test_create_user_existing_user_role_change_is_ignored_and_noted(self):
        profile = UserProfileFactory(user__username="rolechangeuser", role="admin")
        run(username="rolechangeuser", role="finance")
        profile.refresh_from_db()
        self.assertEqual(profile.role, "admin")


class MigrateServiceTest(TestCase):
    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------
    def _create_po_with_number(self, po_number: str, **kwargs: object) -> PurchaseOrder:
        """Create a PO with a fixed purchase_order_number, bypassing the DB trigger."""
        from django.db import connection, transaction

        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("SET LOCAL session_replication_role = 'replica'")
            return PurchaseOrderFactory(purchase_order_number=po_number, **kwargs)

    def _make_link_wb(self, rows: list) -> MagicMock:
        """Return a mock workbook whose 'Master DATA All Item' sheet yields rows[2:]."""
        all_rows = [None, None] + rows  # two header rows
        ws = MagicMock()
        ws.iter_rows.return_value = iter(all_rows)
        wb = MagicMock()
        wb.__getitem__ = MagicMock(return_value=ws)
        return wb

    def test_product_links_updates_supplier_link_for_matching_sku(self) -> None:
        company = CompanyFactory()
        product = ProductFactory(company=company, sku_code="DRS-008")
        ProductSupplierFactory(product=product, company=company, supplier_link=None)

        # col indices: 0-based; col 9 = variant SKU, col 1 = link
        row: list = [None] * 15
        row[9] = "DRS-008-100-BLU"
        row[1] = "https://1688.com/product/1"
        wb = self._make_link_wb([tuple(row)])

        step_product_links(company, wb, io.StringIO())

        from apps.purchasing.models import ProductSupplier

        ps = ProductSupplier.objects.get(product=product)
        self.assertEqual(ps.supplier_link, "https://1688.com/product/1")

    def test_product_links_skips_non_http_links(self) -> None:
        company = CompanyFactory()
        product = ProductFactory(company=company, sku_code="DRS-009")
        ProductSupplierFactory(product=product, company=company, supplier_link=None)

        row: list = [None] * 15
        row[9] = "DRS-009-100-BLU"
        row[1] = "not-a-url"
        wb = self._make_link_wb([tuple(row)])

        step_product_links(company, wb, io.StringIO())

        from apps.purchasing.models import ProductSupplier

        ps = ProductSupplier.objects.get(product=product)
        self.assertIsNone(ps.supplier_link)

    def test_product_links_skips_rows_without_sku_or_link(self) -> None:
        company = CompanyFactory()
        product = ProductFactory(company=company, sku_code="DRS-010")
        ProductSupplierFactory(
            product=product, company=company, supplier_link="https://original.com"
        )

        row: list = [None] * 15  # both sku and link are None
        wb = self._make_link_wb([tuple(row)])

        step_product_links(company, wb, io.StringIO())

        from apps.purchasing.models import ProductSupplier

        ps = ProductSupplier.objects.get(product=product)
        # Not changed because row had no sku/link
        self.assertEqual(ps.supplier_link, "https://original.com")

    # -----------------------------------------------------------------------
    # _parse_po_tracker
    # -----------------------------------------------------------------------
    def _build_tracker_row(self, no: int, overrides: dict | None = None) -> tuple:
        """Build a 35-element tracker row with safe defaults for all relevant columns."""
        from datetime import date

        row: list = [None] * 35
        row[0] = no  # no
        row[1] = date(2025, 1, 10)  # created_date
        row[2] = "INV-001"  # invoice_number
        row[3] = date(2025, 1, 15)  # invoice_date
        row[4] = "RESI-GZ3-001.pdf"  # DO/RESI file
        row[5] = date(2025, 2, 1)  # delivery_date
        row[9] = "ForwarderX"  # forwarder_name
        row[10] = "ShopX"  # shop_services
        row[11] = 0.05  # commission_fee_pct (5%)
        row[12] = "invoice.pdf"  # po_invoice_file
        row[16] = 500_000  # shipping_fee (IDR)
        row[19] = "INVOICE/BRG/81524.pdf"  # do_invoice_file
        row[20] = 0.5  # cbm
        row[23] = 12.0  # weight
        row[30] = 100.0  # delivery_fee_rmb
        if overrides:
            for k, v in overrides.items():
                row[k] = v
        return tuple(row)

    def test_parse_po_tracker_returns_decimal_delivery_fee(self) -> None:
        row = self._build_tracker_row(1)
        wb = _make_tracker_wb({1: [row]})
        result = _parse_po_tracker(wb)
        self.assertIn("PO-2025-001", result)
        fee = result["PO-2025-001"]["delivery_fee_rmb"]
        self.assertIsInstance(fee, Decimal)
        self.assertEqual(fee, Decimal("100.000"))

    def test_parse_po_tracker_maps_tracker_no_to_po_number(self) -> None:
        rows_by_no = {
            1: [self._build_tracker_row(1)],
            8: [self._build_tracker_row(8)],
        }
        wb = _make_tracker_wb(rows_by_no)
        result = _parse_po_tracker(wb)
        self.assertIn("PO-2025-001", result)
        self.assertIn("PO-2026-001", result)

    def test_parse_po_tracker_commission_fee_pct_rounded_integer(self) -> None:
        # 0.05 * 100 = 5
        row = self._build_tracker_row(1, {11: 0.05})
        wb = _make_tracker_wb({1: [row]})
        result = _parse_po_tracker(wb)
        self.assertEqual(result["PO-2025-001"]["commission_fee_pct"], 5)

    # -----------------------------------------------------------------------
    # step_enrich_po_tracker
    # -----------------------------------------------------------------------
    def test_enrich_po_tracker_updates_po_fields(self) -> None:
        company = CompanyFactory()
        warehouse = WarehouseFactory(company=company)
        po = self._create_po_with_number(
            "PO-2025-001",
            company=company,
            warehouse=warehouse,
            total_item_amount=10_000_000,
            exchange_rate=Decimal("2200.000"),
        )

        row = self._build_tracker_row(1, {30: 100.0, 16: 500_000, 11: 0.05})
        wb = _make_tracker_wb({1: [row]})

        step_enrich_po_tracker(company, wb, io.StringIO())

        po.refresh_from_db()
        self.assertEqual(po.invoice_number, "INV-001")
        self.assertEqual(po.delivery_fee, Decimal("100.000"))
        self.assertEqual(po.commission_fee_pct, 5)
        self.assertEqual(po.shipping_fee, 500_000)

    def test_enrich_po_tracker_uses_decimal_not_float(self) -> None:
        company = CompanyFactory()
        warehouse = WarehouseFactory(company=company)
        self._create_po_with_number(
            "PO-2025-001",
            company=company,
            warehouse=warehouse,
            total_item_amount=10_000_000,
            exchange_rate=Decimal("2200.000"),
        )

        # delivery_fee_rmb = 100.001 RMB, exchange_rate = 2200
        # delivery_fee_idr_calc must be Decimal-accurate: int(100.001 * 2200) = 220002
        row = self._build_tracker_row(1, {30: 100.001, 11: 0.0, 16: 0})
        wb = _make_tracker_wb({1: [row]})

        step_enrich_po_tracker(company, wb, io.StringIO())

        from apps.purchasing.models import PurchaseOrder

        po = PurchaseOrder.objects.get(purchase_order_number="PO-2025-001", company=company)
        # total_amount = 10_000_000 + 0 + 0 + 220002 = 10_220_002
        self.assertEqual(po.total_amount, 10_220_002)

    def test_enrich_po_tracker_sets_draft_status_for_late_pos(self) -> None:
        company = CompanyFactory()
        warehouse = WarehouseFactory(company=company)
        self._create_po_with_number(
            "PO-2026-006",
            company=company,
            warehouse=warehouse,
            status="COMPLETED",
        )
        self._create_po_with_number(
            "PO-2026-007",
            company=company,
            warehouse=warehouse,
            status="COMPLETED",
        )

        wb = _make_tracker_wb({})  # no data rows; DRAFT update still runs

        step_enrich_po_tracker(company, wb, io.StringIO())

        from apps.purchasing.models import PurchaseOrder

        for po_num in ("PO-2026-006", "PO-2026-007"):
            po = PurchaseOrder.objects.get(purchase_order_number=po_num, company=company)
            self.assertEqual(po.status, "DRAFT")

    # -----------------------------------------------------------------------
    # step_split_deliveries
    # -----------------------------------------------------------------------
    def test_split_deliveries_creates_split_po_with_moved_details(self) -> None:
        from datetime import date

        from apps.purchasing.models import PurchaseOrder, PurchaseOrderDetail

        company = CompanyFactory()
        warehouse = WarehouseFactory(company=company)
        supplier = SupplierFactory(company=company)

        # Parent PO "PO-2025-001"
        parent_po = self._create_po_with_number(
            "PO-2025-001",
            company=company,
            warehouse=warehouse,
            supplier=supplier,
            status="COMPLETED",
            exchange_rate=Decimal("2200.000"),
            total_item_amount=5_000_000,
            total_ordered_qty=50,
        )

        # A variant whose SKU will appear in the split sheet
        split_variant = ProductVariantFactory(company=company, sku_variant_code="DRS-001-100-BLU")
        # A detail on the parent PO for this variant
        PurchaseOrderDetailFactory(
            company=company,
            purchase_order=parent_po,
            product_variant=split_variant,
            ordered_qty=20,
            total_price_base=2_000_000,
        )

        # Tracker workbook: 2 rows for tracker_no=1 (second row = split)
        split_tracker_row: list = [None] * 35
        split_tracker_row[0] = 1
        split_tracker_row[4] = "RESI-SPLIT.pdf"
        split_tracker_row[5] = date(2025, 5, 15)
        split_tracker_row[16] = 200_000
        split_tracker_row[30] = 50.0
        tracker_row1 = tuple([1] + [None] * 34)

        tracker_wb = _make_tracker_wb({1: [tracker_row1, tuple(split_tracker_row)]})

        # Detail workbook: sheet "PO Recap 2 April 2025", sku_col=8, date_col=16, split_date=2025-05-15
        detail_row: list = [None] * 20
        detail_row[8] = "DRS-001-100-BLU"
        detail_row[16] = date(2025, 5, 15)
        detail_wb = _make_split_detail_wb("PO Recap 2 April 2025", [tuple(detail_row)])

        step_split_deliveries(company, tracker_wb, detail_wb, io.StringIO())

        # Split PO must have been created
        split_po = PurchaseOrder.objects.get(purchase_order_number="PO-2025-001B", company=company)
        self.assertEqual(split_po.status, "COMPLETED")
        self.assertEqual(split_po.supplier_name, "Ancorelala")

        # The detail must have moved to the split PO
        self.assertTrue(
            PurchaseOrderDetail.objects.filter(
                purchase_order=split_po, product_variant=split_variant
            ).exists()
        )
        # Parent PO detail list must be empty (all moved)
        self.assertFalse(
            PurchaseOrderDetail.objects.filter(
                purchase_order=parent_po, product_variant=split_variant
            ).exists()
        )

    def test_split_deliveries_updates_fifo_reference_number(self) -> None:
        from datetime import date

        company = CompanyFactory()
        warehouse = WarehouseFactory(company=company)
        supplier = SupplierFactory(company=company)

        parent_po = self._create_po_with_number(
            "PO-2025-001",
            company=company,
            warehouse=warehouse,
            supplier=supplier,
            status="COMPLETED",
            exchange_rate=Decimal("2200.000"),
        )
        split_variant = ProductVariantFactory(company=company, sku_variant_code="DRS-002-100-RED")
        PurchaseOrderDetailFactory(
            company=company,
            purchase_order=parent_po,
            product_variant=split_variant,
            ordered_qty=10,
            total_price_base=1_000_000,
        )
        # FIFO record pointing at parent PO
        ProductCogsFactory(
            company=company,
            product_variant=split_variant,
            warehouse=warehouse,
            reference_number="PO-2025-001",
        )

        split_tracker_row: list = [None] * 35
        split_tracker_row[0] = 1
        split_tracker_row[4] = "RESI-X.pdf"
        split_tracker_row[5] = date(2025, 5, 15)
        split_tracker_row[16] = 0
        split_tracker_row[30] = 0.0
        tracker_row1 = tuple([1] + [None] * 34)

        tracker_wb = _make_tracker_wb({1: [tracker_row1, tuple(split_tracker_row)]})

        detail_row: list = [None] * 20
        detail_row[8] = "DRS-002-100-RED"
        detail_row[16] = date(2025, 5, 15)
        detail_wb = _make_split_detail_wb("PO Recap 2 April 2025", [tuple(detail_row)])

        step_split_deliveries(company, tracker_wb, detail_wb, io.StringIO())

        from apps.inventory.models import ProductCogs

        cogs = ProductCogs.objects.get(product_variant=split_variant)
        self.assertEqual(cogs.reference_number, "PO-2025-001B")

    def test_split_deliveries_uses_decimal_for_delivery_fee(self) -> None:
        from datetime import date

        from apps.purchasing.models import PurchaseOrder

        company = CompanyFactory()
        warehouse = WarehouseFactory(company=company)
        supplier = SupplierFactory(company=company)

        parent_po = self._create_po_with_number(
            "PO-2025-001",
            company=company,
            warehouse=warehouse,
            supplier=supplier,
            status="COMPLETED",
            exchange_rate=Decimal("2200.000"),
        )
        split_variant = ProductVariantFactory(company=company, sku_variant_code="DRS-003-100-GRN")
        PurchaseOrderDetailFactory(
            company=company,
            purchase_order=parent_po,
            product_variant=split_variant,
            ordered_qty=5,
            total_price_base=500_000,
        )

        # delivery_fee_rmb = 123.456 (float in sheet, must land as Decimal on PO)
        split_tracker_row: list = [None] * 35
        split_tracker_row[0] = 1
        split_tracker_row[4] = "RESI-Y.pdf"
        split_tracker_row[5] = date(2025, 5, 15)
        split_tracker_row[16] = 0
        split_tracker_row[30] = 123.456
        tracker_row1 = tuple([1] + [None] * 34)

        tracker_wb = _make_tracker_wb({1: [tracker_row1, tuple(split_tracker_row)]})

        detail_row: list = [None] * 20
        detail_row[8] = "DRS-003-100-GRN"
        detail_row[16] = date(2025, 5, 15)
        detail_wb = _make_split_detail_wb("PO Recap 2 April 2025", [tuple(detail_row)])

        step_split_deliveries(company, tracker_wb, detail_wb, io.StringIO())

        split_po = PurchaseOrder.objects.get(purchase_order_number="PO-2025-001B", company=company)
        self.assertIsInstance(split_po.delivery_fee, Decimal)
        self.assertEqual(split_po.delivery_fee, Decimal("123.456"))

    # -----------------------------------------------------------------------
    # step_reconciliation
    # -----------------------------------------------------------------------
    def test_reconciliation_warns_for_negative_fifo(self) -> None:
        ProductCogsFactory(remaining_qty=-1)
        out = io.StringIO()
        step_reconciliation(out)
        self.assertIn("[WARN]", out.getvalue())
        self.assertIn("Negative FIFO qty", out.getvalue())

    def test_reconciliation_all_ok_with_no_negatives(self) -> None:
        out = io.StringIO()
        step_reconciliation(out)
        self.assertIn("All checks passed", out.getvalue())


class RoundMoneyToIntTest(TestCase):
    """Pins the report-side half of the money serialization contract: report
    output rounds full-precision money to whole-rupiah integers at the
    serialization boundary. If this rounding is ever removed or reversed
    (e.g. money starts round-tripping unrounded through a report), these tests
    fail.
    """

    def test_rounds_decimal_fraction_half_up(self) -> None:
        self.assertEqual(round_money_to_int(Decimal("100.50")), 101)
        self.assertEqual(round_money_to_int(Decimal("100.49")), 100)
        self.assertEqual(round_money_to_int(Decimal("100.51")), 101)

    def test_returns_int_type_not_decimal_or_float(self) -> None:
        result = round_money_to_int(Decimal("42.10"))
        self.assertIsInstance(result, int)
        self.assertNotIsInstance(result, Decimal)

    def test_none_rounds_to_zero(self) -> None:
        self.assertEqual(round_money_to_int(None), 0)

    def test_plain_integer_passes_through_unchanged(self) -> None:
        self.assertEqual(round_money_to_int(302), 302)

    def test_totals_rule_rounds_the_exact_total_not_the_sum_of_rounded_lines(self) -> None:
        """Three lines of 100.50 sum to an exact total of 301.50, which rounds to
        302. Rounding each line first (101 + 101 + 101 = 303) is the wrong answer
        this mechanism must never produce.
        """
        lines = [Decimal("100.50"), Decimal("100.50"), Decimal("100.50")]
        exact_total = sum(lines, Decimal("0"))

        rounded_exact_total = round_money_to_int(exact_total)
        sum_of_rounded_lines = sum(round_money_to_int(line) for line in lines)

        self.assertEqual(rounded_exact_total, 302)
        self.assertEqual(sum_of_rounded_lines, 303)
        self.assertNotEqual(rounded_exact_total, sum_of_rounded_lines)


class RoundedMoneyFieldSerializerTest(TestCase):
    """Proves the shared mechanism works through an actual DRF serializer, not
    just as a bare function — this is what every report serializer wires in.
    """

    class _MoneyLineSerializer(serializers.Serializer):
        label = serializers.CharField()
        amount = RoundedMoneyField()

    def test_rounds_fractional_decimal_input_to_whole_rupiah_int(self) -> None:
        serializer = self._MoneyLineSerializer({"label": "line-1", "amount": Decimal("100.50")})
        self.assertEqual(serializer.data["amount"], 101)
        self.assertIsInstance(serializer.data["amount"], int)

    def test_totals_rule_through_the_serializer(self) -> None:
        exact_total = Decimal("100.50") + Decimal("100.50") + Decimal("100.50")
        serializer = self._MoneyLineSerializer({"label": "total", "amount": exact_total})
        self.assertEqual(serializer.data["amount"], 302)

    def test_none_serializes_to_none_not_zero(self) -> None:
        """DRF's Serializer.to_representation short-circuits None before calling
        the field, matching a missing/optional money value's usual JSON shape."""

        class _OptionalMoneySerializer(serializers.Serializer):
            amount = RoundedMoneyField(allow_null=True)

        serializer = _OptionalMoneySerializer({"amount": None})
        self.assertIsNone(serializer.data["amount"])
