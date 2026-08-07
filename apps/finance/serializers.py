from rest_framework import serializers

from apps.finance.models import (
    AccountsPayable,
    AccountsReceivable,
    CashTransaction,
    Expense,
    ExpenseCategory,
    PaymentRecord,
)
from core.models import Company
from core.serializers import RoundedMoneyField


class PaymentRecordSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)

    class Meta:
        model = PaymentRecord
        fields = [
            "id",
            "accounts_payable",
            "amount",
            "payment_date",
            "payment_method",
            "reference_number",
            "proof_file",
            "note",
            "cdate",
            "udate",
        ]
        read_only_fields = ["id", "accounts_payable", "cdate", "udate"]


class AccountsPayableSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    po_number = serializers.CharField(source="purchase_order.purchase_order_number", read_only=True)
    purchase_order = serializers.CharField(source="purchase_order.id", read_only=True)
    remaining_amount = serializers.IntegerField(read_only=True)
    payments = PaymentRecordSerializer(many=True, read_only=True)

    class Meta:
        model = AccountsPayable
        fields = [
            "id",
            "purchase_order",
            "po_number",
            "total_amount",
            "paid_amount",
            "remaining_amount",
            "status",
            "due_date",
            "note",
            "payments",
            "cdate",
            "udate",
        ]
        read_only_fields = [
            "id",
            "purchase_order",
            "total_amount",
            "paid_amount",
            "status",
            "cdate",
            "udate",
        ]


class RecordPaymentSerializer(serializers.Serializer):
    amount = serializers.IntegerField(min_value=1)
    payment_date = serializers.DateField()
    payment_method = serializers.ChoiceField(choices=PaymentRecord.PaymentMethod.choices)
    reference_number = serializers.CharField(required=False, default="")
    proof_file = serializers.FileField(required=False)
    note = serializers.CharField(required=False, default="")


class AccountsReceivableSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    order_number = serializers.CharField(source="sales_order.order_number", read_only=True)
    sales_order = serializers.CharField(source="sales_order.id", read_only=True)

    class Meta:
        model = AccountsReceivable
        fields = [
            "id",
            "sales_order",
            "order_number",
            "expected_amount",
            "settled_amount",
            "status",
            "settlement_date",
            "marketplace_settlement_id",
            "note",
            "cdate",
            "udate",
        ]
        read_only_fields = [
            "id",
            "sales_order",
            "expected_amount",
            "cdate",
            "udate",
        ]


class SettleReceivableSerializer(serializers.Serializer):
    settled_amount = serializers.IntegerField(min_value=0)
    settlement_date = serializers.DateField()
    marketplace_settlement_id = serializers.CharField(required=False, default="")


class ExpenseBreakdownItemSerializer(serializers.Serializer):
    category_name = serializers.CharField()
    total_amount = RoundedMoneyField()


class IncomeStatementSerializer(serializers.Serializer):
    period = serializers.DictField()
    gross_revenue = RoundedMoneyField()
    total_discount = RoundedMoneyField()
    net_revenue = RoundedMoneyField()
    marketplace_fees = RoundedMoneyField()
    shipping_cost_seller = RoundedMoneyField()
    cogs = RoundedMoneyField()
    gross_profit = RoundedMoneyField()
    operating_expenses = RoundedMoneyField()
    operating_expenses_breakdown = ExpenseBreakdownItemSerializer(many=True)
    net_profit = RoundedMoneyField()
    net_profit_margin_pct = serializers.FloatField()


class BalanceSheetAssetsSerializer(serializers.Serializer):
    inventory_value = RoundedMoneyField()
    accounts_receivable = RoundedMoneyField()
    total_assets = RoundedMoneyField()


class BalanceSheetLiabilitiesSerializer(serializers.Serializer):
    accounts_payable = RoundedMoneyField()
    total_liabilities = RoundedMoneyField()


class BalanceSheetEquitySerializer(serializers.Serializer):
    retained_earnings = RoundedMoneyField()


class BalanceSheetSerializer(serializers.Serializer):
    as_of = serializers.CharField()
    assets = BalanceSheetAssetsSerializer()
    liabilities = BalanceSheetLiabilitiesSerializer()
    equity = BalanceSheetEquitySerializer()


class CashFlowOperatingSerializer(serializers.Serializer):
    cash_in_sales = RoundedMoneyField()
    cash_out_purchases = RoundedMoneyField()
    cash_out_expenses = RoundedMoneyField()
    net_operating = RoundedMoneyField()


class CashFlowSerializer(serializers.Serializer):
    period = serializers.DictField()
    operating = CashFlowOperatingSerializer()
    net_cash_flow = RoundedMoneyField()


class LowStockVariantSerializer(serializers.Serializer):
    variant_id = serializers.CharField()
    sku = serializers.CharField()
    name = serializers.CharField()
    available_qty = serializers.IntegerField()


class TopSkuSerializer(serializers.Serializer):
    variant_id = serializers.CharField()
    sku = serializers.CharField()
    name = serializers.CharField()
    qty_sold = serializers.IntegerField()
    revenue = RoundedMoneyField()


class DashboardKPISerializer(serializers.Serializer):
    today_orders = serializers.IntegerField()
    today_revenue = RoundedMoneyField()
    mtd_orders = serializers.IntegerField()
    mtd_revenue = RoundedMoneyField()
    mtd_profit = RoundedMoneyField()
    pending_orders = serializers.IntegerField()
    outstanding_ap = RoundedMoneyField()
    low_stock_variants = LowStockVariantSerializer(many=True)
    top_skus_mtd = TopSkuSerializer(many=True)


class StockMovementReportSerializer(serializers.Serializer):
    variant_id = serializers.CharField()
    sku = serializers.CharField()
    name = serializers.CharField()
    beginning_qty = serializers.IntegerField()
    in_purchase = serializers.IntegerField()
    out_sales = serializers.IntegerField()
    adjustments = serializers.IntegerField()
    returns = serializers.IntegerField()
    ending_qty = serializers.IntegerField()
    ending_value = RoundedMoneyField()


class CogsFifoLayerSerializer(serializers.Serializer):
    reference = serializers.CharField()
    qty_consumed = serializers.IntegerField()
    cogs_per_unit = RoundedMoneyField()
    total = RoundedMoneyField()


class CogsReportSerializer(serializers.Serializer):
    order_number = serializers.CharField()
    order_date = serializers.CharField()
    variant_sku = serializers.CharField()
    variant_name = serializers.CharField()
    quantity = serializers.IntegerField()
    selling_price = RoundedMoneyField()
    actual_cogs_per_unit = RoundedMoneyField()
    actual_cogs_total = RoundedMoneyField()
    fifo_layers = CogsFifoLayerSerializer(many=True)


class ExpenseCategorySerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    company = serializers.PrimaryKeyRelatedField(
        queryset=Company.objects.all(), pk_field=serializers.CharField()
    )

    class Meta:
        model = ExpenseCategory
        fields = [
            "id",
            "company",
            "name",
            "description",
            "is_active",
            "cdate",
            "udate",
        ]
        read_only_fields = ["id", "cdate", "udate"]


class ExpenseSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    company = serializers.PrimaryKeyRelatedField(
        queryset=Company.objects.all(), pk_field=serializers.CharField()
    )
    category = serializers.PrimaryKeyRelatedField(
        queryset=ExpenseCategory.objects.all(), pk_field=serializers.CharField()
    )
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = Expense
        fields = [
            "id",
            "company",
            "expense_number",
            "category",
            "category_name",
            "description",
            "amount",
            "expense_date",
            "payment_method",
            "receipt_file",
            "is_recurring",
            "note",
            "cdate",
            "udate",
        ]
        read_only_fields = ["id", "expense_number", "cdate", "udate"]


class ExpenseListSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = Expense
        fields = [
            "id",
            "expense_number",
            "category_name",
            "description",
            "amount",
            "expense_date",
            "payment_method",
            "cdate",
        ]
        read_only_fields = ["id", "expense_number", "cdate"]


class ExpenseSummarySerializer(serializers.Serializer):
    category__name = serializers.CharField()
    total_amount = serializers.IntegerField()
    count = serializers.IntegerField()


class CashTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CashTransaction
        fields = [
            "id",
            "transaction_date",
            "description",
            "amount",
            "transaction_type",
            "category",
            "reference_number",
            "note",
            "cdate",
            "udate",
        ]
        read_only_fields = ["id", "cdate", "udate"]
