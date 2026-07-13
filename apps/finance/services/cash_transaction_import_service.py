"""
Service layer for importing cash transactions from a parsed Excel Finance sheet.

All DB writes are wrapped in a single @transaction.atomic call.
Uses bulk_create for efficiency — no ORM calls inside Python loops.
"""

from dataclasses import dataclass

from django.db import transaction

from core.management.commands.import_cash_transactions_parser import ParsedCashTransaction
from core.models import Company


@dataclass
class CashTransactionImportResult:
    inserted: int


class CashTransactionImportService:
    @transaction.atomic
    def import_cash_transactions(
        self,
        company: Company,
        parsed_rows: list[ParsedCashTransaction],
    ) -> CashTransactionImportResult:
        """
        Import parsed cash transactions for a company into the database.

        Performs a full idempotency reset before inserting:
        - Deletes all CashTransaction rows for the company
        - Bulk-creates all parsed_rows

        Returns a CashTransactionImportResult summary.
        """
        from apps.finance.models import CashTransaction

        # 1. Idempotency reset
        CashTransaction.objects.filter(company=company).delete()

        # 2. Build objects for bulk_create
        objects = [
            CashTransaction(
                company=company,
                transaction_date=row.transaction_date,
                description=row.description,
                amount=row.amount,
                transaction_type=row.transaction_type,
                category=row.category,
                reference_number="",
                note=row.note,
            )
            for row in parsed_rows
        ]

        # 3. Bulk create
        CashTransaction.objects.bulk_create(objects, batch_size=200)

        return CashTransactionImportResult(
            inserted=len(objects),
        )
