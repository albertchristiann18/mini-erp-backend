from decimal import Decimal

from django.test import TestCase

from apps.finance.models import CashTransaction
from apps.finance.tests.factories import (
    AccountsPayableFactory,
    AccountsReceivableFactory,
    CashTransactionFactory,
    ExpenseFactory,
    PaymentRecordFactory,
)


class MoneyFieldsDecimalConversionRegressionTests(TestCase):
    """MONEY-7: finance/0004_alter_accountspayable_paid_amount_and_more widens 7 IDR
    money fields (AccountsPayable.total_amount/paid_amount, PaymentRecord.amount,
    AccountsReceivable.expected_amount/settled_amount, Expense.amount,
    CashTransaction.amount) from BigIntegerField to DecimalField(18, 2).

    Postgres's `ALTER COLUMN TYPE numeric` from bigint is an exact, lossless widening
    conversion — an existing integer value becomes the same value with a zero
    fractional part. These tests prove that end-to-end against the fully-migrated
    schema: a plain integer written through the ORM reads back, after a DB round
    trip, as the identical Decimal value."""

    def test_accounts_payable_int_money_values_round_trip_as_identical_decimal(self):
        ap = AccountsPayableFactory(total_amount=1000000, paid_amount=400000)
        ap.refresh_from_db()

        self.assertIsInstance(ap.total_amount, Decimal)
        self.assertEqual(ap.total_amount, Decimal("1000000.00"))
        self.assertIsInstance(ap.paid_amount, Decimal)
        self.assertEqual(ap.paid_amount, Decimal("400000.00"))

    def test_payment_record_int_amount_round_trips_as_identical_decimal(self):
        payment = PaymentRecordFactory(amount=500000)
        payment.refresh_from_db()

        self.assertIsInstance(payment.amount, Decimal)
        self.assertEqual(payment.amount, Decimal("500000.00"))

    def test_accounts_receivable_int_money_values_round_trip_as_identical_decimal(self):
        ar = AccountsReceivableFactory(expected_amount=750000, settled_amount=300000)
        ar.refresh_from_db()

        self.assertIsInstance(ar.expected_amount, Decimal)
        self.assertEqual(ar.expected_amount, Decimal("750000.00"))
        self.assertIsInstance(ar.settled_amount, Decimal)
        self.assertEqual(ar.settled_amount, Decimal("300000.00"))

    def test_expense_int_amount_round_trips_as_identical_decimal(self):
        expense = ExpenseFactory(amount=100000)
        expense.refresh_from_db()

        self.assertIsInstance(expense.amount, Decimal)
        self.assertEqual(expense.amount, Decimal("100000.00"))

    def test_cash_transaction_int_amount_round_trips_as_identical_decimal(self):
        cash_transaction = CashTransactionFactory(amount=1000000)
        cash_transaction.refresh_from_db()

        self.assertIsInstance(cash_transaction.amount, Decimal)
        self.assertEqual(cash_transaction.amount, Decimal("1000000.00"))


class FractionalMoneyRoundTripTests(TestCase):
    """The whole reason for this ticket: bank interest and its 20% tax are the only
    genuine source of fractional money in the system, and they post through
    CashTransaction. A fractional amount must round-trip exactly through the DB —
    no truncation, no floating-point drift."""

    def test_bank_interest_fractional_amount_round_trips_exactly(self):
        cash_transaction = CashTransactionFactory(
            amount=Decimal("123456.78"),
            category=CashTransaction.TransactionCategory.BANK_INTEREST,
            transaction_type=CashTransaction.TransactionType.INFLOW,
        )
        cash_transaction.refresh_from_db()

        self.assertIsInstance(cash_transaction.amount, Decimal)
        self.assertEqual(cash_transaction.amount, Decimal("123456.78"))
