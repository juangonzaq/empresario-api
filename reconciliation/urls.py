from django.urls import path

from .views import (
    DocumentsView, ExplainView, MovementClassifyView, MovementsView, RunView,
    StatementDeleteView, StatementsView, SummaryView,
)

app_name = "reconciliation"

urlpatterns = [
    path("reconciliation/summary/", SummaryView.as_view(), name="summary"),
    path("reconciliation/run/", RunView.as_view(), name="run"),
    path("reconciliation/documents/", DocumentsView.as_view(), name="documents"),
    path("reconciliation/movements/", MovementsView.as_view(), name="movements"),
    path("reconciliation/movements/<uuid:pk>/classify/", MovementClassifyView.as_view(), name="movement-classify"),
    path("reconciliation/explain/", ExplainView.as_view(), name="explain"),
    path("reconciliation/statements/", StatementsView.as_view(), name="statements"),
    path("reconciliation/statements/<uuid:pk>/", StatementDeleteView.as_view(), name="statement-delete"),
]
