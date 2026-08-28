from django.urls import path

from .views import CruceAnualView, DeclaracionesView, PanoramaView, PlanillaDeclaradaView, RentaAnualView

app_name = "sunat_declaraciones"

urlpatterns = [
    path("declaraciones/", DeclaracionesView.as_view(), name="declaraciones"),
    path("declaraciones/renta-anual/", RentaAnualView.as_view(), name="renta-anual"),
    path("declaraciones/renta-anual/cruce/", CruceAnualView.as_view(), name="renta-anual-cruce"),
    path("declaraciones/panorama/", PanoramaView.as_view(), name="panorama"),
    path("declaraciones/planilla/", PlanillaDeclaradaView.as_view(), name="planilla"),
]
