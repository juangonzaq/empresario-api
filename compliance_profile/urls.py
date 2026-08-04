from rest_framework.routers import DefaultRouter

from .views import ComplianceRatingViewSet

app_name = "compliance_profile"

router = DefaultRouter()
router.register(
    "compliance/ratings", ComplianceRatingViewSet, basename="compliance-rating"
)

urlpatterns = router.urls
