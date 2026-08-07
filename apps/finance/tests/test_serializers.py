from decimal import Decimal

from django.test import TestCase

from apps.finance.serializers import (
    BalanceSheetSerializer,
    CashFlowSerializer,
    CogsReportSerializer,
    DashboardKPISerializer,
    IncomeStatementSerializer,
    StockMovementReportSerializer,
)


class ReportSerializersRoundMoneyToIntTest(TestCase):
    """Pins the report side of the money serialization contract for every named
    report endpoint (income statement, balance sheet, cash flow, dashboard,
    stock movement, COGS): money fields round to whole-rupiah integers at the
    serialization boundary, even when fed full-precision Decimal input (as the
    future Decimal-typed model fields will produce). If a report serializer ever
    stops rounding, or starts emitting Decimal/string output instead, these
    tests fail.
    """

    def test_income_statement_rounds_fractional_money_fields_to_int(self) -> None:
        data = {
            "period": {"start": "2026-01-01", "end": "2026-01-31"},
            "gross_revenue": Decimal("100.50"),
            "total_discount": Decimal("0"),
            "net_revenue": Decimal("100.50"),
            "marketplace_fees": Decimal("0"),
            "shipping_cost_seller": Decimal("0"),
            "cogs": Decimal("0"),
            "gross_profit": Decimal("100.50"),
            "operating_expenses": Decimal("0"),
            "operating_expenses_breakdown": [
                {"category_name": "Rent", "total_amount": Decimal("50.25")}
            ],
            "net_profit": Decimal("100.50"),
            "net_profit_margin_pct": 100.0,
        }
        result = IncomeStatementSerializer(data).data

        self.assertEqual(result["gross_revenue"], 101)
        self.assertIsInstance(result["gross_revenue"], int)
        self.assertEqual(result["net_profit"], 101)
        self.assertEqual(result["operating_expenses_breakdown"][0]["total_amount"], 50)

    def test_income_statement_totals_rule_rounds_exact_total_not_sum_of_lines(self) -> None:
        """Given three lines of 100.50, the total must display 302 (round of the
        exact 301.50 total), not 303 (sum of three lines individually rounded)."""
        exact_gross_revenue = Decimal("100.50") * 3
        data = {
            "period": {"start": "2026-01-01", "end": "2026-01-31"},
            "gross_revenue": exact_gross_revenue,
            "total_discount": Decimal("0"),
            "net_revenue": exact_gross_revenue,
            "marketplace_fees": Decimal("0"),
            "shipping_cost_seller": Decimal("0"),
            "cogs": Decimal("0"),
            "gross_profit": exact_gross_revenue,
            "operating_expenses": Decimal("0"),
            "operating_expenses_breakdown": [],
            "net_profit": exact_gross_revenue,
            "net_profit_margin_pct": 100.0,
        }
        result = IncomeStatementSerializer(data).data

        self.assertEqual(result["gross_revenue"], 302)
        self.assertNotEqual(result["gross_revenue"], 303)

    def test_balance_sheet_rounds_nested_money_fields_to_int(self) -> None:
        data = {
            "as_of": "2026-01-31",
            "assets": {
                "inventory_value": Decimal("1000.75"),
                "accounts_receivable": Decimal("0"),
                "total_assets": Decimal("1000.75"),
            },
            "liabilities": {
                "accounts_payable": Decimal("500.25"),
                "total_liabilities": Decimal("500.25"),
            },
            "equity": {"retained_earnings": Decimal("500.50")},
        }
        result = BalanceSheetSerializer(data).data

        self.assertEqual(result["assets"]["inventory_value"], 1001)
        self.assertEqual(result["liabilities"]["accounts_payable"], 500)
        self.assertEqual(result["equity"]["retained_earnings"], 501)

    def test_cash_flow_rounds_nested_money_fields_to_int(self) -> None:
        data = {
            "period": {"start": "2026-01-01", "end": "2026-01-31"},
            "operating": {
                "cash_in_sales": Decimal("300.50"),
                "cash_out_purchases": Decimal("100.25"),
                "cash_out_expenses": Decimal("50.10"),
                "net_operating": Decimal("150.15"),
            },
            "net_cash_flow": Decimal("150.15"),
        }
        result = CashFlowSerializer(data).data

        self.assertEqual(result["operating"]["cash_in_sales"], 301)
        self.assertEqual(result["net_cash_flow"], 150)

    def test_dashboard_kpis_rounds_money_fields_but_not_counts(self) -> None:
        data = {
            "today_orders": 3,
            "today_revenue": Decimal("200.75"),
            "mtd_orders": 10,
            "mtd_revenue": Decimal("2000.50"),
            "mtd_profit": Decimal("500.25"),
            "pending_orders": 2,
            "outstanding_ap": Decimal("100.10"),
            "low_stock_variants": [],
            "top_skus_mtd": [
                {
                    "variant_id": "v1",
                    "sku": "SKU-1",
                    "name": "Widget",
                    "qty_sold": 5,
                    "revenue": Decimal("100.60"),
                }
            ],
        }
        result = DashboardKPISerializer(data).data

        self.assertEqual(result["today_revenue"], 201)
        self.assertEqual(result["mtd_profit"], 500)
        self.assertEqual(result["today_orders"], 3)
        self.assertEqual(result["top_skus_mtd"][0]["revenue"], 101)
        self.assertEqual(result["top_skus_mtd"][0]["qty_sold"], 5)

    def test_stock_movement_report_rounds_ending_value_not_quantities(self) -> None:
        data = {
            "variant_id": "v1",
            "sku": "SKU-1",
            "name": "Widget",
            "beginning_qty": 10,
            "in_purchase": 5,
            "out_sales": 3,
            "adjustments": 0,
            "returns": 0,
            "ending_qty": 12,
            "ending_value": Decimal("1200.50"),
        }
        result = StockMovementReportSerializer(data).data

        self.assertEqual(result["ending_value"], 1201)
        self.assertEqual(result["ending_qty"], 12)

    def test_cogs_report_rounds_money_fields_including_fifo_layers(self) -> None:
        data = {
            "order_number": "SO-0001",
            "order_date": "2026-01-15",
            "variant_sku": "SKU-1",
            "variant_name": "Widget",
            "quantity": 2,
            "selling_price": Decimal("50000.50"),
            "actual_cogs_per_unit": Decimal("30000.25"),
            "actual_cogs_total": Decimal("60000.50"),
            "fifo_layers": [
                {
                    "reference": "PO-0001",
                    "qty_consumed": 2,
                    "cogs_per_unit": Decimal("30000.25"),
                    "total": Decimal("60000.50"),
                }
            ],
        }
        result = CogsReportSerializer(data).data

        self.assertEqual(result["selling_price"], 50001)
        self.assertEqual(result["actual_cogs_total"], 60001)
        self.assertEqual(result["fifo_layers"][0]["cogs_per_unit"], 30000)
        self.assertEqual(result["fifo_layers"][0]["qty_consumed"], 2)
