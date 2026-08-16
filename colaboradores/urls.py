from rest_framework.routers import DefaultRouter

from .views import ColaboradorViewSet, ContratoViewSet, MemorandumViewSet

app_name = "colaboradores"

router = DefaultRouter()
router.register("colaboradores", ColaboradorViewSet, basename="colaborador")
router.register("memorandums", MemorandumViewSet, basename="memorandum")
router.register("contratos", ContratoViewSet, basename="contrato")

urlpatterns = router.urls
