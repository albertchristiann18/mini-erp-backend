from datetime import date
from typing import Any, Type

from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer

from apps.finance.models import (
    AccountsPayable,
    AccountsReceivable,
    CashTransaction,
    Expense,
    ExpenseCategory,
)
from apps.finance.serializers import (
    AccountsPayableSerializer,
    AccountsReceivableSerializer,
    CashTransactionSerializer,
    ExpenseCategorySerializer,
    ExpenseListSerializer,
    ExpenseSerializer,
    ExpenseSummarySerializer,
    RecordPaymentSerializer,
    SettleReceivableSerializer,
)
from apps.finance.services.accounts_payable_service import AccountsPayableService
from apps.finance.services.expense_service import ExpenseService
from apps.finance.services.report_service import ReportService
from apps.finance.services.stock_report_service import StockReportService
from core.permissions import IsStaffOrReadOnly


class AccountsPayableViewSet(viewsets.ModelViewSet):
    queryset = AccountsPayable.objects.all().select_related("purchase_order")
    serializer_class = AccountsPayableSerializer
    http_method_names = ["get", "patch", "post"]
    permission_classes = [IsStaffOrReadOnly]

    @action(detail=True, methods=["post"], url_path="record-payment")
    def record_payment(self, request: Request, pk: str | None = None) -> Response:
        """POST /accounts-payable/{id}/record-payment/"""
        payable = self.get_object()
        serializer = RecordPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            service = AccountsPayableService()
            payment = service.record_payment(payable, serializer.validated_data)
            return Response(
                {"id": str(payment.id), "amount": payment.amount},
                status=status.HTTP_201_CREATED,
            )
        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class AccountsReceivableViewSet(viewsets.ModelViewSet):
    queryset = AccountsReceivable.objects.all().select_related("sales_order")
    serializer_class = AccountsReceivableSerializer
    http_method_names = ["get", "patch", "post"]
    permission_classes = [IsStaffOrReadOnly]

    @action(detail=True, methods=["post"])
    def settle(self, request: Request, pk: str | None = None) -> Response:
        """POST /accounts-receivable/{id}/settle/"""
        receivable = self.get_object()
        serializer = SettleReceivableSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            service = AccountsPayableService()
            service.settle_receivable(receivable, serializer.validated_data)
            return Response(
                AccountsReceivableSerializer(receivable).data,
                status=status.HTTP_200_OK,
            )
        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class ReportViewSet(viewsets.ViewSet):
    """All report endpoints — read only."""

    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"], url_path="income-statement")
    def income_statement(self, request: Request) -> Response:
        """GET /reports/income-statement/?company_id=&start_date=&end_date="""
        company_id = request.query_params.get("company_id", "")
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        if not start_date or not end_date or not company_id:
            return Response(
                {"error": "company_id, start_date and end_date are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = ReportService().income_statement(
            company_id, date.fromisoformat(start_date), date.fromisoformat(end_date)
        )
        return Response(data)

    @action(detail=False, methods=["get"], url_path="balance-sheet")
    def balance_sheet(self, request: Request) -> Response:
        """GET /reports/balance-sheet/?company_id=&as_of_date="""
        company_id = request.query_params.get("company_id", "")
        as_of_date = request.query_params.get("as_of_date")
        if not as_of_date or not company_id:
            return Response(
                {"error": "company_id and as_of_date are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = ReportService().balance_sheet(company_id, date.fromisoformat(as_of_date))
        return Response(data)

    @action(detail=False, methods=["get"], url_path="cash-flow")
    def cash_flow(self, request: Request) -> Response:
        """GET /reports/cash-flow/?company_id=&start_date=&end_date="""
        company_id = request.query_params.get("company_id", "")
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        if not start_date or not end_date or not company_id:
            return Response(
                {"error": "company_id, start_date and end_date are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = ReportService().cash_flow_statement(
            company_id, date.fromisoformat(start_date), date.fromisoformat(end_date)
        )
        return Response(data)

    @action(detail=False, methods=["get"])
    def dashboard(self, request: Request) -> Response:
        """GET /reports/dashboard/?company_id="""
        company_id = request.query_params.get("company_id", "")
        if not company_id:
            return Response(
                {"error": "company_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = ReportService().dashboard_kpis(company_id)
        return Response(data)

    @action(detail=False, methods=["get"], url_path="stock-movement")
    def stock_movement(self, request: Request) -> Response:
        """GET /reports/stock-movement/?company_id=&start_date=&end_date=&warehouse_id=&variant_id="""
        company_id = request.query_params.get("company_id", "")
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        if not start_date or not end_date or not company_id:
            return Response(
                {"error": "company_id, start_date and end_date are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = StockReportService().stock_movement_report(
            company_id=company_id,
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            warehouse_id=request.query_params.get("warehouse_id"),
            product_variant_id=request.query_params.get("variant_id"),
        )
        return Response(data)

    @action(detail=False, methods=["get"])
    def cogs(self, request: Request) -> Response:
        """GET /reports/cogs/?company_id=&start_date=&end_date=&sales_order_id="""
        company_id = request.query_params.get("company_id", "")
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        if not start_date or not end_date or not company_id:
            return Response(
                {"error": "company_id, start_date and end_date are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = StockReportService().cogs_report(
            company_id=company_id,
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            sales_order_id=request.query_params.get("sales_order_id"),
        )
        return Response(data)


class ExpenseCategoryViewSet(viewsets.ModelViewSet):
    queryset = ExpenseCategory.objects.all()
    serializer_class = ExpenseCategorySerializer
    http_method_names = ["get", "post", "patch"]
    permission_classes = [IsStaffOrReadOnly]


class ExpenseViewSet(viewsets.ModelViewSet):
    queryset = Expense.objects.select_related("category").all()
    http_method_names = ["get", "post", "patch", "delete"]
    permission_classes = [IsStaffOrReadOnly]

    def get_serializer_class(self) -> Type[Serializer]:
        if self.action == "list":
            return ExpenseListSerializer
        if self.action == "summary":
            return ExpenseSummarySerializer
        return ExpenseSerializer

    def get_queryset(self) -> QuerySet[Expense]:
        qs = super().get_queryset()
        category = self.request.query_params.get("category")
        start_date = self.request.query_params.get("start_date")
        end_date = self.request.query_params.get("end_date")

        if category:
            qs = qs.filter(category_id=category)
        if start_date:
            qs = qs.filter(expense_date__gte=start_date)
        if end_date:
            qs = qs.filter(expense_date__lte=end_date)
        return qs  # type: ignore[no-any-return]

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        service = ExpenseService()
        expense = service.create_expense(serializer.validated_data)
        return Response(ExpenseSerializer(expense).data, status=status.HTTP_201_CREATED)

    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        instance = self.get_object()
        serializer = self.get_serializer(
            instance, data=request.data, partial=kwargs.pop("partial", False)
        )
        serializer.is_valid(raise_exception=True)
        service = ExpenseService()
        expense = service.update_expense(instance, serializer.validated_data)
        return Response(ExpenseSerializer(expense).data)

    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        instance = self.get_object()
        service = ExpenseService()
        service.delete_expense(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CashTransactionViewSet(viewsets.ModelViewSet):
    """CRUD for CashTransaction. Supports filtering by transaction_type, category, date range."""

    serializer_class = CashTransactionSerializer
    permission_classes = [IsStaffOrReadOnly]
    http_method_names = ["get", "post", "patch", "delete"]

    def get_queryset(self) -> QuerySet:
        qs = CashTransaction.objects.filter(company=self.request.user.profile.company)
        tx_type = self.request.query_params.get("transaction_type")
        category = self.request.query_params.get("category")
        date_from = self.request.query_params.get("date_from")
        date_to = self.request.query_params.get("date_to")
        if tx_type:
            qs = qs.filter(transaction_type=tx_type)
        if category:
            qs = qs.filter(category=category)
        if date_from:
            qs = qs.filter(transaction_date__gte=date_from)
        if date_to:
            qs = qs.filter(transaction_date__lte=date_to)
        return qs

    def perform_create(self, serializer: CashTransactionSerializer) -> None:
        serializer.save(company=self.request.user.profile.company)

    @action(detail=False, methods=["get"])
    def summary(self, request: Request) -> Response:
        """Returns expense summary grouped by category for a date range.
        Query params: start_date, end_date, company_id"""
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        company_id = request.query_params.get("company_id")

        if not all([start_date, end_date, company_id]):
            return Response(
                {"error": "start_date, end_date, and company_id are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        service = ExpenseService()
        summary = service.get_expense_summary_by_category(company_id, start_date, end_date)
        serializer = ExpenseSummarySerializer(summary, many=True)
        return Response(serializer.data)
