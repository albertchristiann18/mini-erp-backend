from rest_framework.routers import DefaultRouter

from apps.finance.views import (
    AccountsPayableViewSet,
    AccountsReceivableViewSet,
    CashTransactionViewSet,
    ExpenseCategoryViewSet,
    ExpenseViewSet,
    ReportViewSet,
)

router = DefaultRouter()
router.register(r"accounts-payable", AccountsPayableViewSet, basename="accounts-payable")
router.register(r"accounts-receivable", AccountsReceivableViewSet, basename="accounts-receivable")
router.register(r"reports", ReportViewSet, basename="reports")
router.register(r"expense-categories", ExpenseCategoryViewSet, basename="expense-category")
router.register(r"expenses", ExpenseViewSet, basename="expense")
router.register(r"cash-transactions", CashTransactionViewSet, basename="cash-transaction")

urlpatterns = router.urls
