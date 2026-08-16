from django.urls import path

from .views import (
    AiSummaryActionView, AiSummaryView, AlertStatusView, AlertsView,
    ConsistencyView, CreditNotesView, CustomersView, InvoiceInsightView,
    InvoiceOverrideView, ItfView, ManualEntriesView, ManualEntryView,
    OverviewView, PeriodDocumentsView, PurchasesView, SalesView,
    SuppliersView,
)

app_name = "finance_analytics"

urlpatterns = [
    path("finance/overview/", OverviewView.as_view(), name="overview"),
    path("finance/sales/", SalesView.as_view(), name="sales"),
    path("finance/purchases/", PurchasesView.as_view(), name="purchases"),
    path("finance/customers/", CustomersView.as_view(), name="customers"),
    path("finance/suppliers/", SuppliersView.as_view(), name="suppliers"),
    path("finance/credit-notes/", CreditNotesView.as_view(), name="credit-notes"),
    path("finance/documents/", PeriodDocumentsView.as_view(), name="period-documents"),
    path("finance/itf/", ItfView.as_view(), name="itf"),
    path("finance/consistency/", ConsistencyView.as_view(), name="consistency"),
    path("finance/alerts/", AlertsView.as_view(), name="alerts"),
    path("finance/alerts/<uuid:pk>/", AlertStatusView.as_view(), name="alert-status"),
    path("finance/invoices/<uuid:pk>/", InvoiceInsightView.as_view(), name="invoice-insight"),
    path(
        "finance/invoices/<uuid:pk>/override/",
        InvoiceOverrideView.as_view(),
        name="invoice-override",
    ),
    path("finance/entries/", ManualEntriesView.as_view(), name="manual-entries"),
    path("finance/entries/<uuid:pk>/", ManualEntryView.as_view(), name="manual-entry"),
    path("finance/ai-summary/", AiSummaryView.as_view(), name="ai-summary"),
    path(
        "finance/ai-summary/actions/<str:action_id>/",
        AiSummaryActionView.as_view(),
        name="ai-summary-action",
    ),
]
