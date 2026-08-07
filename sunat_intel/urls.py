from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import AskView, CaseViewSet, OverviewView, VigiaHistoryView

app_name = "sunat_intel"

router = DefaultRouter()
router.register("intel/cases", CaseViewSet, basename="case")

urlpatterns = [
    path("intel/overview/", OverviewView.as_view(), name="overview"),
    path("intel/ask/", AskView.as_view(), name="ask"),
    path("intel/vigia/history/", VigiaHistoryView.as_view(), name="vigia-history"),
    *router.urls,
]
