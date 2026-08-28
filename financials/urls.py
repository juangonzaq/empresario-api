from django.urls import path

from .masters import MasterDetailView, MasterListView, MastersIndexView
from .views import (
    BalanceSheetView, BulkCategorizeView, CategoriesView, DrilldownView,
    ExcludeTransactionView, IncomeStatementView, KpisView, MonthlyRatiosView,
    PendingTransactionsView, PeriodsView, RatiosHistoryView, RatiosView, SettleTransactionView,
    StatementLinesView, SyncView, TransactionsView,
)

app_name = "financials"

urlpatterns = [
    path("financials/sync/", SyncView.as_view(), name="sync"),
    path("financials/income-statement/", IncomeStatementView.as_view(), name="income-statement"),
    path("financials/balance-sheet/", BalanceSheetView.as_view(), name="balance-sheet"),
    path("financials/ratios/", RatiosView.as_view(), name="ratios"),
    path("financials/ratios/monthly/", MonthlyRatiosView.as_view(), name="ratios-monthly"),
    path("financials/ratios/history/", RatiosHistoryView.as_view(), name="ratios-history"),
    path("financials/kpis/", KpisView.as_view(), name="kpis"),
    path("financials/periods/", PeriodsView.as_view(), name="periods"),
    path("financials/drilldown/", DrilldownView.as_view(), name="drilldown"),
    path("financials/categories/", CategoriesView.as_view(), name="categories"),
    path(
        "financials/statement-lines/",
        StatementLinesView.as_view(),
        name="statement-lines",
    ),
    path("transactions/", TransactionsView.as_view(), name="transactions"),
    path("transactions/pending/", PendingTransactionsView.as_view(), name="transactions-pending"),
    path("transactions/bulk-categorize/", BulkCategorizeView.as_view(), name="bulk-categorize"),
    path("transactions/<uuid:pk>/exclude/", ExcludeTransactionView.as_view(), name="transaction-exclude"),
    path("transactions/<uuid:pk>/settle/", SettleTransactionView.as_view(), name="transaction-settle"),
    path("masters/", MastersIndexView.as_view(), name="masters-index"),
    path("masters/<str:key>/", MasterListView.as_view(), name="master-list"),
    path("masters/<str:key>/<uuid:pk>/", MasterDetailView.as_view(), name="master-detail"),
]
