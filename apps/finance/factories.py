# mypy: disable-error-code="no-untyped-call"

from datetime import date

import factory

from apps.finance.models import (
    AccountsPayable,
    AccountsReceivable,
    CashTransaction,
    Expense,
    ExpenseCategory,
    PaymentRecord,
)
from apps.inventory.factories import CompanyFactory
from apps.purchasing.factories import PurchaseOrderFactory
from apps.sales.factories import SalesOrderFactory


class AccountsPayableFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AccountsPayable

    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]
    purchase_order = factory.SubFactory(PurchaseOrderFactory)  # type: ignore[no-untyped-call]
    total_amount = 1000000
    paid_amount = 0
    status = AccountsPayable.PaymentStatus.UNPAID


class PaymentRecordFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PaymentRecord

    company = factory.LazyAttribute(lambda o: o.accounts_payable.company)  # type: ignore[no-untyped-call]
    accounts_payable = factory.SubFactory(AccountsPayableFactory)  # type: ignore[no-untyped-call]
    amount = 500000
    payment_date = factory.LazyFunction(lambda: date.today())  # type: ignore[no-untyped-call]
    payment_method = PaymentRecord.PaymentMethod.TRANSFER


class AccountsReceivableFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AccountsReceivable

    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]
    sales_order = factory.SubFactory(SalesOrderFactory)  # type: ignore[no-untyped-call]
    expected_amount = 500000
    settled_amount = 0
    status = AccountsReceivable.SettlementStatus.PENDING


class ExpenseCategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ExpenseCategory

    company = factory.SubFactory(CompanyFactory)
    name = factory.Sequence(lambda n: f"Category {n}")
    is_active = True


class ExpenseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Expense

    company = factory.SubFactory(CompanyFactory)
    category = factory.SubFactory(ExpenseCategoryFactory)
    description = "Test expense"
    amount = 100000
    expense_date = factory.LazyFunction(lambda: date.today())
    payment_method = Expense.PaymentMethod.TRANSFER


class CashTransactionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CashTransaction

    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]
    transaction_date = factory.LazyFunction(lambda: date.today())  # type: ignore[no-untyped-call]
    description = factory.Sequence(lambda n: f"Cash transaction {n}")  # type: ignore[no-untyped-call]
    amount = 1000000
    transaction_type = CashTransaction.TransactionType.INFLOW
    category = CashTransaction.TransactionCategory.EQUITY_INJECTION
    reference_number = ""
    note = ""
