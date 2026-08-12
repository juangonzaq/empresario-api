from django.urls import path

from .views import (
    AfpnetAfiliadoDetalleView, AfpnetConectarView, AfpnetDatosView, AfpnetDesafioView,
    AfpnetDesconectarView, AfpnetEnrolarView, AfpnetEstadoView,
)

app_name = "afpnet"

urlpatterns = [
    path("afpnet/estado/", AfpnetEstadoView.as_view(), name="estado"),
    path("afpnet/desafio/", AfpnetDesafioView.as_view(), name="desafio"),
    path("afpnet/conectar/", AfpnetConectarView.as_view(), name="conectar"),
    path("afpnet/desconectar/", AfpnetDesconectarView.as_view(), name="desconectar"),
    path("afpnet/datos/", AfpnetDatosView.as_view(), name="datos"),
    path("afpnet/enrolar/", AfpnetEnrolarView.as_view(), name="enrolar"),
    path("afpnet/afiliados/<str:cuspp>/", AfpnetAfiliadoDetalleView.as_view(),
         name="afiliado-detalle"),
]
