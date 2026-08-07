from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    ComplianceFindingDetailView,
    ComplianceFindingsView,
    ComplianceRatingViewSet,
    ComplianceSummaryView,
)

app_name = "compliance_profile"

router = DefaultRouter()
router.register(
    "compliance/ratings", ComplianceRatingViewSet, basename="compliance-rating"
)

urlpatterns = [
    path(
        "compliance/summary/",
        ComplianceSummaryView.as_view(),
        name="compliance-summary",
    ),
    path(
        "compliance/findings/",
        ComplianceFindingsView.as_view(),
        name="compliance-findings",
    ),
    path(
        "compliance/findings/<str:code>/",
        ComplianceFindingDetailView.as_view(),
        name="compliance-finding-detail",
    ),
    *router.urls,
]
