"""
Pure parsing functions for the cash transactions import workbook.

No ORM, no I/O — all functions are testable in isolation with in-memory rows.
Implements:
  - Finance sheet column layout (0-indexed, fixed positions)
  - Date parsing (datetime object or pass-through)
  - Decimal parsing (never float)
  - CATEGORY_MAP for transaction type strings
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

CATEGORY_MAP: dict[str, str] = {
    "In (In)": "EQUITY_INJECTION",
    "In (Sales)": "SALES_SETTLEMENT",
    "In (Others)": "OTHER_INCOME",
    "Out (Order)": "OTHER_EXPENSE",
    "Out (Ops)": "OTHER_EXPENSE",
    "Out (Marketing)": "OTHER_EXPENSE",
    "Out (Shipping)": "OTHER_EXPENSE",
    "Out (Salary)": "OTHER_EXPENSE",
    "Out (Others)": "OTHER_EXPENSE",
}


@dataclass
class ParsedCashTransaction:
    transaction_date: date
    description: str
    amount: Decimal  # always positive
    transaction_type: str  # "INFLOW" | "OUTFLOW"
    category: str  # one of CashTransaction.TransactionCategory values
    note: str


def _parse_decimal(val: object) -> Decimal | None:
    """
    Parse a value as Decimal. Returns None for blank/None values.
    Never uses float arithmetic.
    """
    if val is None:
        return None
    str_val = str(val).strip()
    if str_val == "":
        return None
    try:
        return Decimal(str_val)
    except InvalidOperation:
        return None


def parse_cash_transactions_sheet(rows: list[tuple]) -> list[ParsedCashTransaction]:
    """
    Parse rows from the Finance sheet (rows[0] is the header, skip it).

    Column layout (0-indexed):
      col 0: row number (skip if None or non-integer)
      col 2: transaction_date (datetime object → .date(), or pass through)
      col 3: description (str, max 2000 chars, default "")
      col 4: type1 — "In" or "Out" (default "Out")
      col 5: type string → mapped to category via CATEGORY_MAP
      col 6: value (numeric, skip row if None or unparseable, skip if zero after abs)

    Skip rows where: col 0 is None/non-int, col 6 is None/unparseable/zero.
    Use Decimal for value parsing, never float.
    Returns list of ParsedCashTransaction — one per valid row.
    """
    results: list[ParsedCashTransaction] = []

    for data_row in rows[1:]:
        # col 0: row number guard
        row_num = data_row[0] if len(data_row) > 0 else None
        if row_num is None:
            continue
        try:
            int(row_num)
        except (ValueError, TypeError):
            continue

        # col 2: transaction_date
        raw_date = data_row[2] if len(data_row) > 2 else None
        if raw_date is None:
            continue
        if isinstance(raw_date, datetime):
            transaction_date: date = raw_date.date()
        else:
            transaction_date = raw_date  # type: ignore[assignment]

        # col 3: description
        raw_desc = data_row[3] if len(data_row) > 3 else None
        description = str(raw_desc).strip()[:2000] if raw_desc else ""

        # col 4: type1 ("In" or "Out")
        raw_type1 = data_row[4] if len(data_row) > 4 else None
        type1 = str(raw_type1).strip() if raw_type1 else "Out"

        # col 5: type string → category
        raw_type = data_row[5] if len(data_row) > 5 else None
        type_str = str(raw_type).strip() if raw_type else ""

        # col 6: value (IDR) — Decimal, never float
        raw_value = data_row[6] if len(data_row) > 6 else None
        if raw_value is None:
            continue
        decimal_value = _parse_decimal(raw_value)
        if decimal_value is None:
            continue
        amount = abs(decimal_value)
        if amount == 0:
            continue

        # Derived fields
        transaction_type = "INFLOW" if type1 == "In" else "OUTFLOW"
        category = CATEGORY_MAP.get(type_str, "OTHER_EXPENSE")
        note = f"Excel Type: {type_str}"

        results.append(
            ParsedCashTransaction(
                transaction_date=transaction_date,
                description=description,
                amount=amount,
                transaction_type=transaction_type,
                category=category,
                note=note,
            )
        )

    return results
