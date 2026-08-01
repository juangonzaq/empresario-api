from rest_framework.routers import DefaultRouter

from .views import SupplierCheckViewSet, SupplierViewSet

app_name = "suppliers"

router = DefaultRouter()
router.register("suppliers", SupplierViewSet, basename="supplier")
router.register("supplier-checks", SupplierCheckViewSet, basename="suppliercheck")

urlpatterns = router.urls
