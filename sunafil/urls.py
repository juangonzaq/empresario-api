from rest_framework.routers import DefaultRouter

from .views import SunafilItemViewSet

app_name = "sunafil"

router = DefaultRouter()
router.register("sunafil", SunafilItemViewSet, basename="sunafilitem")

urlpatterns = router.urls
