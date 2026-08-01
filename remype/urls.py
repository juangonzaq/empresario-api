from rest_framework.routers import DefaultRouter

from .views import RemypeViewSet

app_name = "remype"

router = DefaultRouter()
router.register("remype", RemypeViewSet, basename="remype")

urlpatterns = router.urls
