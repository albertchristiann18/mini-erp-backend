from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet

from apps.finance.models import AccountsPayable, AccountsReceivable, PaymentRecord
from apps.purchasing.models import PurchaseOrder
from apps.sales.models import SalesOrder


class AccountsPayableService:
    @transaction.atomic
    def create_payable_from_po(self, po: PurchaseOrder) -> AccountsPayable:
        """Create AccountsPayable from a PurchaseOrder when PO transitions to ORDERED."""
        ap, _ = AccountsPayable.objects.get_or_create(
            purchase_order=po,
            defaults={
                "company": po.company,
                "total_amount": po.total_amount or 0,
            },
        )
        return ap

    @transaction.atomic
    def record_payment(self, payable: AccountsPayable, data: dict) -> PaymentRecord:
        """
        Record a payment against an AP.
        Raises ValidationError if amount > remaining_amount.
        """
        payable = AccountsPayable.objects.select_for_update().get(id=payable.id)
        amount = data["amount"]
        if amount > payable.remaining_amount:
            raise ValidationError(
                {
                    "amount": f"Payment amount {amount} exceeds remaining amount {payable.remaining_amount}."
                }
            )

        payment = PaymentRecord.objects.create(
            company=payable.company,
            accounts_payable=payable,
            amount=amount,
            payment_date=data["payment_date"],
            payment_method=data["payment_method"],
            reference_number=data.get("reference_number", ""),
            proof_file=data.get("proof_file"),
            note=data.get("note", ""),
        )

        payable.paid_amount += amount
        if payable.paid_amount >= payable.total_amount:
            payable.status = AccountsPayable.PaymentStatus.PAID
        elif payable.paid_amount > 0:
            payable.status = AccountsPayable.PaymentStatus.PARTIAL
        else:
            payable.status = AccountsPayable.PaymentStatus.UNPAID
        payable.save()

        return payment

    def get_outstanding(self, company_id: str) -> QuerySet[AccountsPayable]:
        """Return queryset of UNPAID and PARTIAL AP records for a company."""
        return AccountsPayable.objects.filter(
            company_id=company_id,
            status__in=[
                AccountsPayable.PaymentStatus.UNPAID,
                AccountsPayable.PaymentStatus.PARTIAL,
            ],
        )

    @transaction.atomic
    def create_receivable_from_so(self, so: SalesOrder) -> AccountsReceivable:
        """Create AccountsReceivable from a SalesOrder when SO transitions to COMPLETED."""
        ar, _ = AccountsReceivable.objects.get_or_create(
            sales_order=so,
            defaults={
                "company": so.company,
                "expected_amount": so.net_revenue,
            },
        )
        return ar

    @transaction.atomic
    def settle_receivable(self, receivable: AccountsReceivable, data: dict) -> AccountsReceivable:
        """Mark AR as settled."""
        receivable = AccountsReceivable.objects.select_for_update().get(id=receivable.id)
        receivable.settled_amount = data["settled_amount"]
        receivable.settlement_date = data["settlement_date"]
        receivable.marketplace_settlement_id = data.get("marketplace_settlement_id", "")
        receivable.status = AccountsReceivable.SettlementStatus.SETTLED
        receivable.save()
        return receivable
