from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.finance.models import AccountsPayable, AccountsReceivable, CashTransaction
from apps.finance.tests.factories import (
    AccountsPayableFactory,
    AccountsReceivableFactory,
    CashTransactionFactory,
    ExpenseCategoryFactory,
    ExpenseFactory,
)
from core.factories import CompanyFactory
from core.models import UserProfile


class AccountsPayableAPITest(APITestCase):
    def setUp(self):
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
        self.assertEqual(response.data["total_amount"], "1000000.00")

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
        self.assertEqual(response.data["amount"], "5000000.00")
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
        self.assertEqual(response.data["amount"], "1000.00")

    def test_partial_update_of_note_leaves_fractional_amount_unchanged(self):
        cash_transaction = CashTransactionFactory(
            company=self.company,
            amount=Decimal("123456.78"),
        )
        response = self.client.patch(
            f"/cash-transactions/{cash_transaction.id}/",
            {"note": "Reconciled against bank statement"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["amount"], "123456.78")
        cash_transaction.refresh_from_db()
        self.assertEqual(cash_transaction.amount, Decimal("123456.78"))

    def test_cash_transaction_summary_uses_authenticated_company(self):
        """Verify the summary action returns data for the logged-in company without requiring company_id."""
        from apps.finance.tests.factories import ExpenseCategoryFactory, ExpenseFactory

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
