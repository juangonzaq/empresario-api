from django.urls import path

from .views import (
    ActionDetailView, ComplianceOverviewView, ObligationActionsView,
    ObligationDetailView, ObligationEvidenceView, ObligationListView, RecalculateView,
)

app_name = "obligations"

urlpatterns = [
    path("obligations/overview/", ComplianceOverviewView.as_view(), name="overview"),
    path("obligations/", ObligationListView.as_view(), name="list"),
    path("obligations/recalculate/", RecalculateView.as_view(), name="recalculate"),
    path("obligations/actions/<uuid:action_id>/", ActionDetailView.as_view(), name="action-detail"),
    path("obligations/<uuid:obligation_id>/", ObligationDetailView.as_view(), name="detail"),
    path("obligations/<uuid:obligation_id>/evidence/", ObligationEvidenceView.as_view(), name="evidence"),
    path("obligations/<uuid:obligation_id>/actions/", ObligationActionsView.as_view(), name="actions"),
]
