from rest_framework.routers import DefaultRouter

from .views import ItfRecordViewSet

app_name = "sunat_itf"

router = DefaultRouter()
router.register("itf/records", ItfRecordViewSet, basename="itf-record")

urlpatterns = router.urls
