from decimal import Decimal

from django.db import models
from django_ulid.models import ULIDField

from core.models import DefaultModel
from core.utils import generate_ulid


class AccountsPayable(DefaultModel):
    """Tracks PO payment status. Auto-created when PO moves to ORDERED."""

    class PaymentStatus(models.TextChoices):
        UNPAID = "UNPAID", "Unpaid"
        PARTIAL = "PARTIAL", "Partially Paid"
        PAID = "PAID", "Paid"

    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="accounts_payable_id"
    )
    purchase_order = models.OneToOneField(
        "purchasing.PurchaseOrder", on_delete=models.CASCADE, related_name="payable"
    )
    total_amount = models.DecimalField(max_digits=18, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID
    )
    due_date = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True, default="")

    @property
    def remaining_amount(self) -> Decimal:
        return self.total_amount - self.paid_amount

    def __str__(self) -> str:
        return f"AP-{self.purchase_order.pk} ({self.status})"


class PaymentRecord(DefaultModel):
    """Individual payments against an AccountsPayable."""

    class PaymentMethod(models.TextChoices):
        TRANSFER = "TRANSFER", "Bank Transfer"
        CASH = "CASH", "Cash"
        EWALLET = "EWALLET", "E-Wallet"

    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="payment_record_id"
    )
    accounts_payable = models.ForeignKey(
        AccountsPayable, on_delete=models.CASCADE, related_name="payments"
    )
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    payment_date = models.DateField()
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    reference_number = models.CharField(max_length=255, blank=True, default="")
    proof_file = models.FileField(upload_to="finance/payment_proofs/", null=True, blank=True)
    note = models.TextField(blank=True, default="")

    def __str__(self) -> str:
        return f"Payment {self.amount} for {self.accounts_payable}"


class AccountsReceivable(DefaultModel):
    """Marketplace settlement tracking. Auto-created when SO moves to COMPLETED."""

    class SettlementStatus(models.TextChoices):
        PENDING = "PENDING", "Pending Settlement"
        SETTLED = "SETTLED", "Settled"

    id = ULIDField(
        primary_key=True,
        default=generate_ulid,
        editable=False,
        db_column="accounts_receivable_id",
    )
    sales_order = models.OneToOneField(
        "sales.SalesOrder", on_delete=models.CASCADE, related_name="receivable"
    )
    expected_amount = models.DecimalField(max_digits=18, decimal_places=2)
    settled_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(
        max_length=20, choices=SettlementStatus.choices, default=SettlementStatus.PENDING
    )
    settlement_date = models.DateField(null=True, blank=True)
    marketplace_settlement_id = models.CharField(max_length=255, blank=True, default="")
    note = models.TextField(blank=True, default="")

    def __str__(self) -> str:
        return f"AR-{self.sales_order.pk} ({self.status})"


class ExpenseCategory(DefaultModel):
    id = ULIDField(
        primary_key=True, default=generate_ulid, editable=False, db_column="expense_category_id"
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "expenses_expensecategory"
        unique_together = ["company", "name"]

    def __str__(self) -> str:
        return self.name


class Expense(DefaultModel):
    class PaymentMethod(models.TextChoices):
        CASH = "CASH", "Cash"
        TRANSFER = "TRANSFER", "Bank Transfer"
        EWALLET = "EWALLET", "E-Wallet"
        CREDIT = "CREDIT", "Credit Card"

    id = ULIDField(primary_key=True, default=generate_ulid, editable=False, db_column="expense_id")
    expense_number = models.CharField(max_length=100, unique=True, editable=False, default="")
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name="expenses")
    description = models.TextField()
    amount = models.DecimalField(max_digits=18, decimal_places=2)  # IDR
    expense_date = models.DateField()
    payment_method = models.CharField(
        max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.TRANSFER
    )
    receipt_file = models.FileField(upload_to="expenses/receipts/", null=True, blank=True)
    is_recurring = models.BooleanField(default=False)
    note = models.TextField(blank=True, default="")

    class Meta:
        db_table = "expenses_expense"
        ordering = ["-expense_date"]
        indexes = [
            models.Index(fields=["expense_date"]),
            models.Index(fields=["category"]),
        ]

    def __str__(self) -> str:
        return f"{self.expense_number} - {self.description}"


class CashTransaction(DefaultModel):
    """
    Tracks cash movements not covered by SalesOrder or Expense:
    equity injections, founder loans, bank interest, supplier refunds,
    marketplace bulk settlement withdrawals.
    """

    class TransactionType(models.TextChoices):
        INFLOW = "INFLOW", "Inflow"
        OUTFLOW = "OUTFLOW", "Outflow"

    class TransactionCategory(models.TextChoices):
        SALES_SETTLEMENT = "SALES_SETTLEMENT", "Sales Settlement (Marketplace Withdrawal)"
        EQUITY_INJECTION = "EQUITY_INJECTION", "Equity Injection"
        FOUNDER_LOAN = "FOUNDER_LOAN", "Founder Loan"
        BANK_INTEREST = "BANK_INTEREST", "Bank Interest"
        SUPPLIER_REFUND = "SUPPLIER_REFUND", "Supplier Refund"
        OTHER_INCOME = "OTHER_INCOME", "Other Income"
        OTHER_EXPENSE = "OTHER_EXPENSE", "Other Expense / Miscellaneous"

    id = ULIDField(
        primary_key=True,
        default=generate_ulid,
        editable=False,
        db_column="cash_transaction_id",
    )
    transaction_date = models.DateField()
    description = models.TextField()
    amount = models.DecimalField(max_digits=18, decimal_places=2)  # IDR, always positive
    transaction_type = models.CharField(max_length=10, choices=TransactionType.choices)
    category = models.CharField(max_length=30, choices=TransactionCategory.choices)
    reference_number = models.CharField(max_length=255, blank=True, default="")
    note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-transaction_date", "-cdate"]
        indexes = [
            models.Index(fields=["transaction_date"]),
            models.Index(fields=["transaction_type", "category"]),
        ]

    def __str__(self) -> str:
        return f"{self.transaction_type} {self.category} {self.amount} on {self.transaction_date}"
