from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.catalog.tests.factories import ProductFactory, ProductVariantFactory
from apps.finance.models import AccountsPayable, AccountsReceivable, PaymentRecord
from apps.finance.services.accounts_payable_service import AccountsPayableService
from apps.finance.services.report_service import ReportService
from apps.finance.services.stock_report_service import StockReportService
from apps.finance.tests.factories import (
    AccountsPayableFactory,
    AccountsReceivableFactory,
    ExpenseCategoryFactory,
    ExpenseFactory,
    PaymentRecordFactory,
)
from apps.inventory.models import StockMovement
from apps.inventory.tests.factories import ProductCogsFactory
from apps.purchasing.tests.factories import PurchaseOrderFactory
from apps.sales.models import SalesOrder, SalesOrderCogsDetail
from apps.sales.tests.factories import SalesOrderFactory, SalesOrderItemFactory
from core.factories import CompanyFactory, WarehouseFactory


class AccountsPayableServiceTest(TestCase):
    def setUp(self):
        self.service = AccountsPayableService()
        self.company = CompanyFactory()

    def test_create_payable_from_po(self):
        po = PurchaseOrderFactory(company=self.company, total_amount=2000000)
        ap = self.service.create_payable_from_po(po)
        self.assertEqual(ap.total_amount, 2000000)
        self.assertEqual(ap.paid_amount, 0)
        self.assertEqual(ap.status, AccountsPayable.PaymentStatus.UNPAID)
        self.assertEqual(ap.company, self.company)

    def test_record_payment_updates_status_to_partial(self):
        ap = AccountsPayableFactory(company=self.company, total_amount=1000000)
        payment = self.service.record_payment(
            ap,
            {
                "amount": 400000,
                "payment_date": date.today(),
                "payment_method": PaymentRecord.PaymentMethod.TRANSFER,
            },
        )
        ap.refresh_from_db()
        self.assertEqual(payment.amount, 400000)
        self.assertEqual(ap.paid_amount, 400000)
        self.assertEqual(ap.status, AccountsPayable.PaymentStatus.PARTIAL)

    def test_record_payment_updates_status_to_paid(self):
        ap = AccountsPayableFactory(company=self.company, total_amount=1000000)
        self.service.record_payment(
            ap,
            {
                "amount": 1000000,
                "payment_date": date.today(),
                "payment_method": PaymentRecord.PaymentMethod.CASH,
            },
        )
        ap.refresh_from_db()
        self.assertEqual(ap.paid_amount, 1000000)
        self.assertEqual(ap.status, AccountsPayable.PaymentStatus.PAID)

    def test_record_payment_exceeds_remaining_raises_error(self):
        ap = AccountsPayableFactory(company=self.company, total_amount=1000000)
        with self.assertRaises(ValidationError):
            self.service.record_payment(
                ap,
                {
                    "amount": 1500000,
                    "payment_date": date.today(),
                    "payment_method": PaymentRecord.PaymentMethod.TRANSFER,
                },
            )

    def test_get_outstanding_returns_unpaid_and_partial(self):
        ap1 = AccountsPayableFactory(
            company=self.company, status=AccountsPayable.PaymentStatus.UNPAID
        )
        ap2 = AccountsPayableFactory(
            company=self.company, status=AccountsPayable.PaymentStatus.PARTIAL
        )
        AccountsPayableFactory(company=self.company, status=AccountsPayable.PaymentStatus.PAID)
        qs = self.service.get_outstanding(str(self.company.id))
        self.assertEqual(qs.count(), 2)
        ids = set(qs.values_list("id", flat=True))
        self.assertIn(ap1.id, ids)
        self.assertIn(ap2.id, ids)

    def test_create_receivable_from_so(self):
        so = SalesOrderFactory(company=self.company, net_revenue=750000)
        ar = self.service.create_receivable_from_so(so)
        self.assertEqual(ar.expected_amount, 750000)
        self.assertEqual(ar.status, AccountsReceivable.SettlementStatus.PENDING)

    def test_create_receivable_from_so_preserves_fractional_net_revenue_exactly(self):
        so = SalesOrderFactory(company=self.company, net_revenue=Decimal("750000.60"))
        ar = self.service.create_receivable_from_so(so)
        self.assertEqual(ar.expected_amount, Decimal("750000.60"))

    def test_settle_receivable(self):
        ar = AccountsReceivableFactory(company=self.company, expected_amount=500000)
        result = self.service.settle_receivable(
            ar,
            {
                "settled_amount": 500000,
                "settlement_date": date.today(),
                "marketplace_settlement_id": "MKP-123",
            },
        )
        result.refresh_from_db()
        self.assertEqual(result.status, AccountsReceivable.SettlementStatus.SETTLED)
        self.assertEqual(result.settled_amount, 500000)
        self.assertEqual(result.marketplace_settlement_id, "MKP-123")


class ReportServiceTest(TestCase):
    def setUp(self):
        self.service = ReportService()
        self.company = CompanyFactory()
        self.today = date.today()
        self.month_start = self.today.replace(day=1)

    def test_income_statement_with_sales_and_expenses(self):
        SalesOrderFactory(
            company=self.company,
            status=SalesOrder.OrderStatus.COMPLETED,
            order_date=timezone.now(),
            subtotal=1000000,
            total_discount=100000,
            net_revenue=800000,
            total_marketplace_fee=50000,
            shipping_fee_seller=20000,
            total_cogs=300000,
            gross_profit=430000,
        )
        cat = ExpenseCategoryFactory(company=self.company, name="Office")
        ExpenseFactory(
            company=self.company,
            category=cat,
            amount=50000,
            expense_date=self.today,
        )

        result = self.service.income_statement(str(self.company.id), self.month_start, self.today)
        self.assertEqual(result["gross_revenue"], 1000000)
        self.assertEqual(result["net_revenue"], 800000)
        self.assertEqual(result["operating_expenses"], 50000)
        self.assertEqual(result["net_profit"], 430000 - 50000)

    def test_income_statement_empty_period(self):
        result = self.service.income_statement(
            str(self.company.id),
            self.today - timedelta(days=30),
            self.today - timedelta(days=20),
        )
        self.assertEqual(result["gross_revenue"], 0)
        self.assertEqual(result["net_profit"], 0)

    def test_balance_sheet_with_inventory_and_ap(self):
        warehouse = WarehouseFactory(company=self.company)
        product = ProductFactory(company=self.company)
        variant = ProductVariantFactory(product=product)
        ProductCogsFactory(
            company=self.company,
            product_variant=variant,
            warehouse=warehouse,
            remaining_qty=10,
            cogs_amount=5000,
        )
        po = PurchaseOrderFactory(company=self.company)
        AccountsPayableFactory(
            company=self.company,
            purchase_order=po,
            total_amount=1000000,
            paid_amount=200000,
            status=AccountsPayable.PaymentStatus.PARTIAL,
        )

        result = self.service.balance_sheet(str(self.company.id), self.today)
        self.assertEqual(result["assets"]["inventory_value"], 50000)
        self.assertEqual(result["liabilities"]["accounts_payable"], 800000)

    def test_cash_flow_statement(self):
        so = SalesOrderFactory(company=self.company)
        AccountsReceivableFactory(
            company=self.company,
            sales_order=so,
            settled_amount=300000,
            settlement_date=self.today,
            status=AccountsReceivable.SettlementStatus.SETTLED,
        )
        ap = AccountsPayableFactory(company=self.company)
        PaymentRecordFactory(
            accounts_payable=ap,
            amount=100000,
            payment_date=self.today,
        )
        ExpenseFactory(company=self.company, amount=50000, expense_date=self.today)

        result = self.service.cash_flow_statement(
            str(self.company.id), self.month_start, self.today
        )
        self.assertEqual(result["operating"]["cash_in_sales"], 300000)
        self.assertEqual(result["operating"]["cash_out_purchases"], 100000)
        self.assertEqual(result["operating"]["cash_out_expenses"], 50000)
        self.assertEqual(result["operating"]["net_operating"], 300000 - 100000 - 50000)

    def test_dashboard_kpis(self):
        SalesOrderFactory(
            company=self.company,
            status=SalesOrder.OrderStatus.CONFIRMED,
            order_date=timezone.now(),
            net_revenue=200000,
            gross_profit=100000,
        )
        result = self.service.dashboard_kpis(str(self.company.id))
        self.assertEqual(result["today_orders"], 1)
        self.assertEqual(result["today_revenue"], 200000)
        self.assertIn("low_stock_variants", result)
        self.assertIn("top_skus_mtd", result)


class StockReportServiceTest(TestCase):
    def setUp(self):
        self.service = StockReportService()
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.product = ProductFactory(company=self.company)
        self.variant = ProductVariantFactory(
            product=self.product,
            total_available_qty=100,
            current_cogs=5000,
        )

    def test_stock_movement_report(self):
        # Create an inbound movement
        StockMovement.objects.create(
            company=self.company,
            product_variant=self.variant,
            warehouse=self.warehouse,
            movement_type=StockMovement.MovementType.INBOUND,
            quantity=50,
            field_change="physical_qty",
            balance_before=50,
            balance_after=100,
        )
        result = self.service.stock_movement_report(
            company_id=str(self.company.id),
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + timedelta(days=1),
        )
        self.assertTrue(len(result) >= 1)
        row = next(r for r in result if r["variant_id"] == str(self.variant.id))
        self.assertEqual(row["in_purchase"], 50)
        self.assertEqual(row["ending_qty"], 100)
        self.assertEqual(row["ending_value"], 100 * 5000)

    def test_cogs_report(self):
        so = SalesOrderFactory(
            company=self.company,
            status=SalesOrder.OrderStatus.COMPLETED,
            order_date=timezone.now(),
        )
        item = SalesOrderItemFactory(
            sales_order=so,
            product_variant=self.variant,
            quantity=5,
            selling_price=10000,
            actual_cogs_per_unit=5000,
            actual_cogs_total=25000,
        )
        cogs_layer = ProductCogsFactory(
            company=self.company,
            product_variant=self.variant,
            warehouse=self.warehouse,
        )
        SalesOrderCogsDetail.objects.create(
            company=self.company,
            sales_order_item=item,
            product_cogs=cogs_layer,
            quantity_consumed=5,
            cogs_per_unit=5000,
            total_cogs=25000,
        )

        result = self.service.cogs_report(
            company_id=str(self.company.id),
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + timedelta(days=1),
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["quantity"], 5)
        self.assertEqual(result[0]["actual_cogs_total"], 25000)
        self.assertEqual(len(result[0]["fifo_layers"]), 1)


class EdgeCaseFinanceTests(TestCase):
    """Tests for edge case fixes in finance."""

    def setUp(self):
        self.service = AccountsPayableService()
        self.company = CompanyFactory()

    # Fix 3: Lock AP in record_payment (test that payment still works correctly)
    def test_record_payment_with_lock(self):
        ap = AccountsPayableFactory(company=self.company, total_amount=1000000)
        payment = self.service.record_payment(
            ap,
            {
                "amount": 500000,
                "payment_date": date.today(),
                "payment_method": PaymentRecord.PaymentMethod.TRANSFER,
            },
        )
        self.assertEqual(payment.amount, 500000)
        ap.refresh_from_db()
        self.assertEqual(ap.paid_amount, 500000)

    # Fix 4: get_or_create for AP (idempotent)
    def test_create_payable_from_po_idempotent(self):
        from apps.purchasing.tests.factories import PurchaseOrderFactory as POFactory

        po = POFactory(company=self.company, total_amount=2000000)
        ap1 = self.service.create_payable_from_po(po)
        ap2 = self.service.create_payable_from_po(po)
        self.assertEqual(ap1.id, ap2.id)
        self.assertEqual(AccountsPayable.objects.filter(purchase_order=po).count(), 1)

    # Fix 4: get_or_create for AR (idempotent)
    def test_create_receivable_from_so_idempotent(self):
        so = SalesOrderFactory(company=self.company, net_revenue=500000)
        ar1 = self.service.create_receivable_from_so(so)
        ar2 = self.service.create_receivable_from_so(so)
        self.assertEqual(ar1.id, ar2.id)
        self.assertEqual(AccountsReceivable.objects.filter(sales_order=so).count(), 1)

    # Fix 12: Validate expense amount
    def test_create_expense_zero_amount_raises_error(self):
        from apps.finance.services.expense_service import ExpenseService
        from apps.finance.tests.factories import ExpenseCategoryFactory

        cat = ExpenseCategoryFactory(company=self.company)
        with self.assertRaises(ValidationError):
            ExpenseService().create_expense(
                {
                    "company": self.company,
                    "category": cat,
                    "description": "Test",
                    "amount": 0,
                    "expense_date": date.today(),
                }
            )

    def test_create_expense_negative_amount_raises_error(self):
        from apps.finance.services.expense_service import ExpenseService
        from apps.finance.tests.factories import ExpenseCategoryFactory

        cat = ExpenseCategoryFactory(company=self.company)
        with self.assertRaises(ValidationError):
            ExpenseService().create_expense(
                {
                    "company": self.company,
                    "category": cat,
                    "description": "Test",
                    "amount": -5000,
                    "expense_date": date.today(),
                }
            )


class CashTransactionParserTest(TestCase):
    """Tests for parse_cash_transactions_sheet (pure parsing, no DB)."""

    def _make_row(
        self,
        row_num: object = 1,
        col1: object = None,
        transaction_date: object = date(2025, 4, 8),
        description: object = "Chip In Albert",
        type1: object = "In",
        type_str: object = "In (In)",
        value: object = 25000000,
    ) -> tuple:
        return (row_num, col1, transaction_date, description, type1, type_str, value)

    def test_happy_path_inflow_row(self):
        from core.parsers.import_cash_transactions_parser import (
            parse_cash_transactions_sheet,
        )

        header = ("No", None, "Date", "Desc", "Type1", "Type", "Value")
        row = self._make_row(
            row_num=1,
            transaction_date=date(2025, 4, 8),
            description="Chip In Albert",
            type1="In",
            type_str="In (In)",
            value=25000000,
        )
        result = parse_cash_transactions_sheet([header, row])
        self.assertEqual(len(result), 1)
        parsed = result[0]
        self.assertEqual(parsed.transaction_date, date(2025, 4, 8))
        self.assertEqual(parsed.description, "Chip In Albert")
        self.assertEqual(parsed.transaction_type, "INFLOW")
        self.assertEqual(parsed.amount, 25000000)
        self.assertEqual(parsed.category, "EQUITY_INJECTION")
        self.assertEqual(parsed.note, "Excel Type: In (In)")

    def test_happy_path_outflow_row(self):
        from core.parsers.import_cash_transactions_parser import (
            parse_cash_transactions_sheet,
        )

        header = ("No", None, "Date", "Desc", "Type1", "Type", "Value")
        row = self._make_row(
            row_num=2,
            transaction_date=date(2025, 5, 1),
            description="Server costs",
            type1="Out",
            type_str="Out (Ops)",
            value=500000,
        )
        result = parse_cash_transactions_sheet([header, row])
        self.assertEqual(len(result), 1)
        parsed = result[0]
        self.assertEqual(parsed.transaction_type, "OUTFLOW")
        self.assertEqual(parsed.category, "OTHER_EXPENSE")
        self.assertEqual(parsed.amount, 500000)

    def test_decimal_precision_no_float(self):
        from core.parsers.import_cash_transactions_parser import (
            parse_cash_transactions_sheet,
        )

        header = ("No", None, "Date", "Desc", "Type1", "Type", "Value")
        row = self._make_row(value=12345678)
        result = parse_cash_transactions_sheet([header, row])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].amount, 12345678)

    def test_fractional_value_passes_through_without_truncation(self):
        from core.parsers.import_cash_transactions_parser import (
            parse_cash_transactions_sheet,
        )

        header = ("No", None, "Date", "Desc", "Type1", "Type", "Value")
        row = self._make_row(value="12345.67")
        result = parse_cash_transactions_sheet([header, row])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].amount, Decimal("12345.67"))

    def test_skip_row_no_row_number(self):
        from core.parsers.import_cash_transactions_parser import (
            parse_cash_transactions_sheet,
        )

        header = ("No", None, "Date", "Desc", "Type1", "Type", "Value")
        row = self._make_row(row_num=None)
        result = parse_cash_transactions_sheet([header, row])
        self.assertEqual(result, [])

    def test_skip_row_non_integer_row_number(self):
        from core.parsers.import_cash_transactions_parser import (
            parse_cash_transactions_sheet,
        )

        header = ("No", None, "Date", "Desc", "Type1", "Type", "Value")
        row = self._make_row(row_num="abc")
        result = parse_cash_transactions_sheet([header, row])
        self.assertEqual(result, [])

    def test_skip_row_no_value(self):
        from core.parsers.import_cash_transactions_parser import (
            parse_cash_transactions_sheet,
        )

        header = ("No", None, "Date", "Desc", "Type1", "Type", "Value")
        row = self._make_row(value=None)
        result = parse_cash_transactions_sheet([header, row])
        self.assertEqual(result, [])

    def test_skip_row_unparseable_value(self):
        from core.parsers.import_cash_transactions_parser import (
            parse_cash_transactions_sheet,
        )

        header = ("No", None, "Date", "Desc", "Type1", "Type", "Value")
        row = self._make_row(value="N/A")
        result = parse_cash_transactions_sheet([header, row])
        self.assertEqual(result, [])

    def test_unknown_category_defaults_to_other_expense(self):
        from core.parsers.import_cash_transactions_parser import (
            parse_cash_transactions_sheet,
        )

        header = ("No", None, "Date", "Desc", "Type1", "Type", "Value")
        row = self._make_row(type_str="Unknown Type")
        result = parse_cash_transactions_sheet([header, row])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].category, "OTHER_EXPENSE")

    def test_description_truncated_to_2000_chars(self):
        from core.parsers.import_cash_transactions_parser import (
            parse_cash_transactions_sheet,
        )

        header = ("No", None, "Date", "Desc", "Type1", "Type", "Value")
        long_desc = "x" * 2500
        row = self._make_row(description=long_desc)
        result = parse_cash_transactions_sheet([header, row])
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0].description), 2000)


class CashTransactionImportServiceTest(TestCase):
    """Tests for CashTransactionImportService (requires DB)."""

    def setUp(self):
        from core.factories import CompanyFactory

        self.company = CompanyFactory()

    def _make_parsed_row(
        self,
        transaction_date: date | None = None,
        description: str = "Test",
        amount: int = 1000000,
        transaction_type: str = "INFLOW",
        category: str = "EQUITY_INJECTION",
        note: str = "Excel Type: In (In)",
    ) -> "object":
        from core.parsers.import_cash_transactions_parser import ParsedCashTransaction

        return ParsedCashTransaction(
            transaction_date=transaction_date or date(2025, 4, 8),
            description=description,
            amount=amount,
            transaction_type=transaction_type,
            category=category,
            note=note,
        )

    def test_import_inserts_rows(self):
        from apps.finance.models import CashTransaction
        from apps.finance.services.cash_transaction_import_service import (
            CashTransactionImportService,
        )

        rows = [self._make_parsed_row(), self._make_parsed_row(description="Row 2")]
        CashTransactionImportService().import_cash_transactions(
            company=self.company,
            parsed_rows=rows,
        )
        self.assertEqual(CashTransaction.objects.filter(company=self.company).count(), 2)

    def test_import_is_idempotent(self):
        from apps.finance.models import CashTransaction
        from apps.finance.services.cash_transaction_import_service import (
            CashTransactionImportService,
        )

        rows = [self._make_parsed_row(), self._make_parsed_row(description="Row 2")]
        service = CashTransactionImportService()
        service.import_cash_transactions(
            company=self.company,
            parsed_rows=rows,
        )
        service.import_cash_transactions(
            company=self.company,
            parsed_rows=rows,
        )
        self.assertEqual(CashTransaction.objects.filter(company=self.company).count(), 2)

    def test_import_sets_amount_always_positive(self):
        from apps.finance.models import CashTransaction
        from apps.finance.services.cash_transaction_import_service import (
            CashTransactionImportService,
        )

        rows = [
            self._make_parsed_row(amount=5000000),
            self._make_parsed_row(amount=300000, transaction_type="OUTFLOW"),
        ]
        CashTransactionImportService().import_cash_transactions(
            company=self.company,
            parsed_rows=rows,
        )
        amounts = list(
            CashTransaction.objects.filter(company=self.company).values_list("amount", flat=True)
        )
        for amt in amounts:
            self.assertGreater(amt, 0)

    def test_import_result_counts_match(self):
        from apps.finance.services.cash_transaction_import_service import (
            CashTransactionImportService,
        )

        rows = [self._make_parsed_row(), self._make_parsed_row(description="Row 2")]
        result = CashTransactionImportService().import_cash_transactions(
            company=self.company,
            parsed_rows=rows,
        )
        self.assertEqual(result.inserted, 2)
