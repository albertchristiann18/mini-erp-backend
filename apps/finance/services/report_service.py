from datetime import date

from django.db.models import F, Sum

from apps.catalog.models import ProductVariant
from apps.finance.models import AccountsPayable, AccountsReceivable, Expense, PaymentRecord
from apps.inventory.models import ProductCogs
from apps.sales.models import SalesOrder, SalesOrderItem


class ReportService:
    def income_statement(self, company_id: str, start_date: date, end_date: date) -> dict:
        """Aggregate from SalesOrder WHERE status in (COMPLETED, DELIVERED) AND order_date in range."""
        qs = SalesOrder.objects.filter(
            company_id=company_id,
            status__in=[SalesOrder.OrderStatus.COMPLETED, SalesOrder.OrderStatus.DELIVERED],
            order_date__date__gte=start_date,
            order_date__date__lte=end_date,
        )

        agg = qs.aggregate(
            gross_revenue=Sum("subtotal"),
            total_discount=Sum("total_discount"),
            net_revenue=Sum("net_revenue"),
            marketplace_fees=Sum("total_marketplace_fee"),
            shipping_cost_seller=Sum("shipping_fee_seller"),
            cogs=Sum("total_cogs"),
            gross_profit=Sum("gross_profit"),
        )

        for key in agg:
            if agg[key] is None:
                agg[key] = 0

        # Operating expenses
        expenses_qs = Expense.objects.filter(
            company_id=company_id,
            expense_date__gte=start_date,
            expense_date__lte=end_date,
        )
        operating_expenses = expenses_qs.aggregate(total=Sum("amount"))["total"] or 0

        breakdown = list(
            expenses_qs.values(category_name=F("category__name"))
            .annotate(total_amount=Sum("amount"))
            .order_by("-total_amount")
        )

        net_profit = agg["gross_profit"] - operating_expenses
        net_revenue = agg["net_revenue"]
        net_profit_margin_pct = (net_profit / net_revenue * 100) if net_revenue else 0.0

        return {
            "period": {"start": str(start_date), "end": str(end_date)},
            "gross_revenue": agg["gross_revenue"],
            "total_discount": agg["total_discount"],
            "net_revenue": net_revenue,
            "marketplace_fees": agg["marketplace_fees"],
            "shipping_cost_seller": agg["shipping_cost_seller"],
            "cogs": agg["cogs"],
            "gross_profit": agg["gross_profit"],
            "operating_expenses": operating_expenses,
            "operating_expenses_breakdown": breakdown,
            "net_profit": net_profit,
            "net_profit_margin_pct": round(net_profit_margin_pct, 2),
        }

    def balance_sheet(self, company_id: str, as_of_date: date) -> dict:
        """Returns balance sheet as of a given date."""
        # Assets
        inventory_value = (
            ProductCogs.objects.filter(
                product_variant__product__company_id=company_id,
                remaining_qty__gt=0,
            ).aggregate(total=Sum(F("remaining_qty") * F("cogs_amount")))["total"]
            or 0
        )

        accounts_receivable = (
            AccountsReceivable.objects.filter(
                company_id=company_id,
                status=AccountsReceivable.SettlementStatus.PENDING,
            ).aggregate(total=Sum(F("expected_amount") - F("settled_amount")))["total"]
            or 0
        )

        total_assets = inventory_value + accounts_receivable

        # Liabilities
        accounts_payable = (
            AccountsPayable.objects.filter(
                company_id=company_id,
            )
            .exclude(status=AccountsPayable.PaymentStatus.PAID)
            .aggregate(total=Sum(F("total_amount") - F("paid_amount")))["total"]
            or 0
        )

        total_liabilities = accounts_payable

        return {
            "as_of": str(as_of_date),
            "assets": {
                "inventory_value": inventory_value,
                "accounts_receivable": accounts_receivable,
                "total_assets": total_assets,
            },
            "liabilities": {
                "accounts_payable": accounts_payable,
                "total_liabilities": total_liabilities,
            },
            "equity": {
                "retained_earnings": total_assets - total_liabilities,
            },
        }

    def cash_flow_statement(self, company_id: str, start_date: date, end_date: date) -> dict:
        """Returns cash flow statement for a period."""
        cash_in_sales = (
            AccountsReceivable.objects.filter(
                company_id=company_id,
                settlement_date__gte=start_date,
                settlement_date__lte=end_date,
            ).aggregate(total=Sum("settled_amount"))["total"]
            or 0
        )

        cash_out_purchases = (
            PaymentRecord.objects.filter(
                accounts_payable__company_id=company_id,
                payment_date__gte=start_date,
                payment_date__lte=end_date,
            ).aggregate(total=Sum("amount"))["total"]
            or 0
        )

        cash_out_expenses = (
            Expense.objects.filter(
                company_id=company_id,
                expense_date__gte=start_date,
                expense_date__lte=end_date,
            ).aggregate(total=Sum("amount"))["total"]
            or 0
        )

        net_operating = cash_in_sales - cash_out_purchases - cash_out_expenses

        return {
            "period": {"start": str(start_date), "end": str(end_date)},
            "operating": {
                "cash_in_sales": cash_in_sales,
                "cash_out_purchases": cash_out_purchases,
                "cash_out_expenses": cash_out_expenses,
                "net_operating": net_operating,
            },
            "net_cash_flow": net_operating,
        }

    def dashboard_kpis(self, company_id: str) -> dict:
        """Returns dashboard KPIs."""
        from django.utils import timezone

        today = timezone.now().date()
        month_start = today.replace(day=1)

        # Today's orders
        today_orders_qs = SalesOrder.objects.filter(
            company_id=company_id,
            order_date__date=today,
        ).exclude(status=SalesOrder.OrderStatus.CANCELLED)

        today_orders = today_orders_qs.count()
        today_revenue = (
            today_orders_qs.filter(
                status__in=[
                    SalesOrder.OrderStatus.CONFIRMED,
                    SalesOrder.OrderStatus.SHIPPING,
                    SalesOrder.OrderStatus.DELIVERED,
                    SalesOrder.OrderStatus.COMPLETED,
                ]
            ).aggregate(total=Sum("net_revenue"))["total"]
            or 0
        )

        # MTD
        mtd_qs = SalesOrder.objects.filter(
            company_id=company_id,
            order_date__date__gte=month_start,
            order_date__date__lte=today,
        ).exclude(status=SalesOrder.OrderStatus.CANCELLED)

        mtd_orders = mtd_qs.count()
        mtd_agg = mtd_qs.filter(
            status__in=[
                SalesOrder.OrderStatus.CONFIRMED,
                SalesOrder.OrderStatus.SHIPPING,
                SalesOrder.OrderStatus.DELIVERED,
                SalesOrder.OrderStatus.COMPLETED,
            ]
        ).aggregate(
            revenue=Sum("net_revenue"),
            profit=Sum("gross_profit"),
        )
        mtd_revenue = mtd_agg["revenue"] or 0
        mtd_profit = mtd_agg["profit"] or 0

        # Pending orders
        pending_orders = SalesOrder.objects.filter(
            company_id=company_id,
            status=SalesOrder.OrderStatus.PENDING,
        ).count()

        # Outstanding AP
        outstanding_ap = (
            AccountsPayable.objects.filter(
                company_id=company_id,
                status__in=[
                    AccountsPayable.PaymentStatus.UNPAID,
                    AccountsPayable.PaymentStatus.PARTIAL,
                ],
            ).aggregate(total=Sum(F("total_amount") - F("paid_amount")))["total"]
            or 0
        )

        # Low stock variants (threshold = 10)
        low_stock_threshold = 10
        low_stock_qs = ProductVariant.objects.filter(
            product__company_id=company_id,
            total_available_qty__lte=low_stock_threshold,
            is_active=True,
        )
        low_stock_variants = [
            {
                "variant_id": str(v.id),
                "sku": v.sku_variant_code,
                "name": v.name,
                "available_qty": v.total_available_qty,
            }
            for v in low_stock_qs
        ]

        # Top SKUs MTD (top 10 by qty sold)
        top_skus_mtd = list(
            SalesOrderItem.objects.filter(
                sales_order__company_id=company_id,
                sales_order__order_date__date__gte=month_start,
                sales_order__order_date__date__lte=today,
                sales_order__status__in=[
                    SalesOrder.OrderStatus.CONFIRMED,
                    SalesOrder.OrderStatus.SHIPPING,
                    SalesOrder.OrderStatus.DELIVERED,
                    SalesOrder.OrderStatus.COMPLETED,
                ],
            )
            .values(
                variant_id=F("product_variant__id"),
                sku=F("product_variant__sku_variant_code"),
                name=F("product_variant__name"),
            )
            .annotate(
                qty_sold=Sum("quantity"),
                revenue=Sum("line_total"),
            )
            .order_by("-qty_sold")[:10]
        )

        return {
            "today_orders": today_orders,
            "today_revenue": today_revenue,
            "mtd_orders": mtd_orders,
            "mtd_revenue": mtd_revenue,
            "mtd_profit": mtd_profit,
            "pending_orders": pending_orders,
            "outstanding_ap": outstanding_ap,
            "low_stock_variants": low_stock_variants,
            "top_skus_mtd": top_skus_mtd,
        }
