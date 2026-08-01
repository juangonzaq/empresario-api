from rest_framework.routers import DefaultRouter

from .views import RucProfileViewSet

app_name = "ruc_profile"

router = DefaultRouter()
router.register("ruc-profiles", RucProfileViewSet, basename="rucprofile")

urlpatterns = router.urls
