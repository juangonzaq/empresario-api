from django.urls import path

from .views import api_calendario

app_name = "sensor_sunat"

urlpatterns = [
    path("calendario/", api_calendario, name="calendario"),
]
