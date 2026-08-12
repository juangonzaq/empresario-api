from django.urls import path

from .views import api_calendario
from .views_app import (
    CalendarioPropioView, CalendarioResumenView, CalendarioSuscripcionRotarView,
    CalendarioSuscripcionView,
)

app_name = "sensor_sunat"

urlpatterns = [
    # Generador público: el RUC llega por parámetro a propósito (lead magnet).
    path("calendario/", api_calendario, name="calendario"),
    # De la empresa de quien llama.
    path("calendario/mio/", CalendarioPropioView.as_view(), name="calendario-mio"),
    path("calendario/mio/resumen/", CalendarioResumenView.as_view(),
         name="calendario-resumen"),
    path("calendario/mio/suscripcion/rotar/", CalendarioSuscripcionRotarView.as_view(),
         name="calendario-suscripcion-rotar"),
    # Abierta por token: la consultan Google Calendar y Apple Calendar, que no
    # pueden autenticarse. Ver views_app para qué expone y qué no.
    path("calendario/suscripcion/<str:token>.ics", CalendarioSuscripcionView.as_view(),
         name="calendario-suscripcion"),
]
