from rest_framework.routers import DefaultRouter

from .views import ElectronicInvoiceViewSet

app_name = "sunat_cpe"

router = DefaultRouter()
router.register("cpe/invoices", ElectronicInvoiceViewSet, basename="cpe-invoice")

urlpatterns = router.urls
