from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.catalog.factories import ProductFactory, ProductVariantFactory
from apps.finance.factories import (
    AccountsPayableFactory,
    AccountsReceivableFactory,
    CashTransactionFactory,
    ExpenseCategoryFactory,
    ExpenseFactory,
    PaymentRecordFactory,
)
from apps.finance.models import AccountsPayable, AccountsReceivable, CashTransaction, PaymentRecord
from apps.finance.services.accounts_payable_service import AccountsPayableService
from apps.finance.services.report_service import ReportService
from apps.finance.services.stock_report_service import StockReportService
from apps.inventory.factories import ProductCogsFactory
from apps.inventory.models import StockMovement
from apps.purchasing.factories import PurchaseOrderFactory
from apps.sales.factories import SalesOrderFactory, SalesOrderItemFactory
from apps.sales.models import SalesOrder, SalesOrderCogsDetail
from core.factories import CompanyFactory, WarehouseFactory
from core.models import UserProfile

# ---- Service Tests ----


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


# ---- API Tests ----


class AccountsPayableAPITest(APITestCase):
    def setUp(self):
        from core.models import UserProfile

        self.client = APIClient()
        self.company = CompanyFactory()
        self.user = User.objects.create_user(
            username="ap_api_user", password="password", is_staff=True
        )
        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_list_accounts_payable(self):
        AccountsPayableFactory(company=self.company)
        AccountsPayableFactory(company=self.company)
        response = self.client.get("/accounts-payable/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 2)

    def test_get_single_accounts_payable(self):
        ap = AccountsPayableFactory(company=self.company, total_amount=1000000)
        response = self.client.get(f"/accounts-payable/{ap.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total_amount"], 1000000)

    def test_record_payment_partial(self):
        ap = AccountsPayableFactory(company=self.company, total_amount=1000000)
        response = self.client.post(
            f"/accounts-payable/{ap.id}/record-payment/",
            {
                "amount": 400000,
                "payment_date": str(date.today()),
                "payment_method": "TRANSFER",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        ap.refresh_from_db()
        self.assertEqual(ap.status, AccountsPayable.PaymentStatus.PARTIAL)

    def test_record_payment_full_marks_paid(self):
        ap = AccountsPayableFactory(company=self.company, total_amount=1000000)
        response = self.client.post(
            f"/accounts-payable/{ap.id}/record-payment/",
            {
                "amount": 1000000,
                "payment_date": str(date.today()),
                "payment_method": "CASH",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        ap.refresh_from_db()
        self.assertEqual(ap.status, AccountsPayable.PaymentStatus.PAID)

    def test_record_payment_exceeds_remaining_fails(self):
        ap = AccountsPayableFactory(company=self.company, total_amount=1000000)
        response = self.client.post(
            f"/accounts-payable/{ap.id}/record-payment/",
            {
                "amount": 1500000,
                "payment_date": str(date.today()),
                "payment_method": "TRANSFER",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class AccountsReceivableAPITest(APITestCase):
    def setUp(self):
        from core.models import UserProfile

        self.client = APIClient()
        self.company = CompanyFactory()
        self.user = User.objects.create_user(
            username="ar_api_user", password="password", is_staff=True
        )
        UserProfile.objects.create(user=self.user, company=self.company, role="admin")
        self.client.force_authenticate(user=self.user)

    def test_list_accounts_receivable(self):
        AccountsReceivableFactory(company=self.company)
        response = self.client.get("/accounts-receivable/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)

    def test_settle_receivable(self):
        ar = AccountsReceivableFactory(company=self.company, expected_amount=500000)
        response = self.client.post(
            f"/accounts-receivable/{ar.id}/settle/",
            {
                "settled_amount": 500000,
                "settlement_date": str(date.today()),
                "marketplace_settlement_id": "MKP-456",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ar.refresh_from_db()
        self.assertEqual(ar.status, AccountsReceivable.SettlementStatus.SETTLED)


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
        from apps.purchasing.factories import PurchaseOrderFactory as POFactory

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
        from apps.finance.factories import ExpenseCategoryFactory
        from apps.finance.services.expense_service import ExpenseService

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
        from apps.finance.factories import ExpenseCategoryFactory
        from apps.finance.services.expense_service import ExpenseService

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


class CompanyScopedViewsTest(APITestCase):
    """Tests for company-scoped data isolation in finance views."""

    def setUp(self):
        self.company_a = CompanyFactory()
        self.company_b = CompanyFactory()
        self.user_a = User.objects.create_user(
            username="fin_user_a", password="password", is_staff=True
        )
        self.user_b = User.objects.create_user(
            username="fin_user_b", password="password", is_staff=True
        )
        from core.models import UserProfile

        UserProfile.objects.create(user=self.user_a, company=self.company_a, role="admin")
        UserProfile.objects.create(user=self.user_b, company=self.company_b, role="admin")

        self.ap_a = AccountsPayableFactory(company=self.company_a)
        self.ap_b = AccountsPayableFactory(company=self.company_b)

        self.ar_a = AccountsReceivableFactory(company=self.company_a)
        self.ar_b = AccountsReceivableFactory(company=self.company_b)

        cat_a = ExpenseCategoryFactory(company=self.company_a)
        cat_b = ExpenseCategoryFactory(company=self.company_b)

        self.expense_cat_a = cat_a
        self.expense_cat_b = cat_b
        self.expense_a = ExpenseFactory(company=self.company_a, category=cat_a)
        self.expense_b = ExpenseFactory(company=self.company_b, category=cat_b)

    def test_accounts_payable_list_scoped_by_company(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get("/accounts-payable/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [ap["id"] for ap in response.data["results"]]
        self.assertIn(str(self.ap_a.id), ids)
        self.assertNotIn(str(self.ap_b.id), ids)

    def test_accounts_receivable_list_scoped_by_company(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get("/accounts-receivable/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [ar["id"] for ar in response.data["results"]]
        self.assertIn(str(self.ar_a.id), ids)
        self.assertNotIn(str(self.ar_b.id), ids)

    def test_expense_list_scoped_by_company(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get("/expenses/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [e["id"] for e in response.data["results"]]
        self.assertIn(str(self.expense_a.id), ids)
        self.assertNotIn(str(self.expense_b.id), ids)

    def test_report_uses_authenticated_company(self):
        self.client.force_authenticate(user=self.user_a)
        from datetime import date, timedelta

        today = date.today()
        start = today - timedelta(days=30)
        response = self.client.get(
            f"/reports/income-statement/?start_date={start}&end_date={today}"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class CashTransactionAPITest(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.company = CompanyFactory()
        user = User.objects.create_user(username="cash-tx-test", password="password", is_staff=True)
        UserProfile.objects.create(user=user, company=self.company, role="admin")
        self.client.force_authenticate(user=user)

    def test_create_inflow(self):
        payload = {
            "transaction_date": str(date.today()),
            "description": "Equity injection",
            "amount": 5000000,
            "transaction_type": "INFLOW",
            "category": "EQUITY_INJECTION",
        }
        response = self.client.post("/cash-transactions/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["amount"], 5000000)
        self.assertEqual(response.data["transaction_type"], "INFLOW")

    def test_list_cash_transactions(self):
        CashTransactionFactory(company=self.company)
        CashTransactionFactory(company=self.company)
        CashTransactionFactory(company=self.company)
        response = self.client.get("/cash-transactions/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data["results"]), 3)

    def test_filter_by_transaction_type(self):
        CashTransactionFactory(
            company=self.company, transaction_type=CashTransaction.TransactionType.INFLOW
        )
        CashTransactionFactory(
            company=self.company, transaction_type=CashTransaction.TransactionType.OUTFLOW
        )
        response = self.client.get("/cash-transactions/?transaction_type=INFLOW")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for item in response.data["results"]:
            self.assertEqual(item["transaction_type"], "INFLOW")

    def test_filter_by_date_from(self):
        CashTransactionFactory(
            company=self.company,
            transaction_date=date.today() - timedelta(days=5),
        )
        CashTransactionFactory(
            company=self.company,
            transaction_date=date.today(),
        )
        response = self.client.get(
            f"/cash-transactions/?date_from={date.today() - timedelta(days=2)}"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for item in response.data["results"]:
            self.assertGreaterEqual(
                date.fromisoformat(item["transaction_date"]),
                date.today() - timedelta(days=2),
            )

    def test_amount_always_positive(self):
        payload = {
            "transaction_date": str(date.today()),
            "description": "Positive amount test",
            "amount": 1000,
            "transaction_type": "OUTFLOW",
            "category": "OTHER_EXPENSE",
        }
        response = self.client.post("/cash-transactions/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["amount"], 1000)

    def test_cash_transaction_summary_uses_authenticated_company(self):
        """Verify the summary action returns data for the logged-in company without requiring company_id."""
        from apps.finance.factories import ExpenseCategoryFactory, ExpenseFactory

        cat = ExpenseCategoryFactory(company=self.company)
        ExpenseFactory(
            company=self.company,
            category=cat,
            amount=100000,
            expense_date=date.today(),
        )
        start = date.today() - timedelta(days=7)
        end = date.today() + timedelta(days=1)
        response = self.client.get(f"/cash-transactions/summary/?start_date={start}&end_date={end}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data), 0)
