"""Calendario **de la empresa de quien llama**.

Separado a propósito de ``views.py``, que sirve el generador público abierto
(el *lead magnet*): allí el RUC llega por parámetro porque cualquiera puede
consultar el cronograma de cualquier RUC —son fechas derivadas del último
dígito, no datos de nadie—. Aquí no: el RUC sale de la membresía de quien
llama, igual que en el resto de la aplicación, y la respuesta lleva además
alertas y buzón, que **sí** son datos de la empresa.

La única ruta abierta de este módulo es la suscripción por token, y existe
porque Google Calendar y Apple Calendar no mandan cabeceras de autenticación:
la URL secreta es la única credencial posible. Sirve solo el .ics del
cronograma —nunca alertas ni buzón—, para que el peor caso de una URL filtrada
sea revelar fechas que ya se derivan del RUC.
"""

from __future__ import annotations

from urllib.parse import quote

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Organization, TaxRegime
from accounts.tenancy import ManagedOrganizationAPIView, OrganizationAPIView

from . import panel
from .calendario import DATA, a_ics, grupo_de
from .contexto import contexto_de, eventos_de


def _hoy():
    """La fecha en la zona del proyecto (America/Lima), no la del servidor.

    Con UTC, entre las 19:00 y la medianoche de Lima el calendario adelantaba
    un día: un vencimiento de mañana aparecía como «vence hoy».
    """
    return timezone.localdate()


def _urls_suscripcion(request: Request, organization: Organization) -> dict:
    """Las tres formas de suscribirse, ya montadas."""
    ruta = f"/api/calendario/suscripcion/{organization.calendar_token}.ics"
    https = request.build_absolute_uri(ruta)
    webcal = https.replace("https://", "webcal://").replace("http://", "webcal://")
    return {
        "ics": https,
        "webcal": webcal,
        # Google acepta la URL del .ics en `cid`; con webcal:// la reconoce como
        # suscripción viva y no como importación de una copia congelada.
        "google": f"https://calendar.google.com/calendar/r?cid={quote(webcal, safe='')}",
        # En desarrollo la URL apunta a localhost y ningún calendario externo
        # puede alcanzarla. Se dice, en vez de dejar que el usuario descubra
        # solo que la suscripción nunca sincroniza.
        "alcanzable": not request.get_host().startswith(("localhost", "127.0.0.1")),
    }


class CalendarioPropioView(ManagedOrganizationAPIView):
    """El calendario completo de la empresa activa, con su contexto.

    ``GET`` devuelve todo lo que la pantalla necesita en una sola respuesta.
    ``PATCH`` declara el régimen tributario, que es el único parámetro que no
    podemos deducir de ninguna fuente sincronizada.
    """

    def get(self, request: Request) -> Response:
        organization = request.organization
        hoy = _hoy()
        contexto = contexto_de(organization)
        eventos = eventos_de(organization, desde=hoy, con_cumpleanos=True)

        return Response({
            **contexto.as_dict(),
            "grupo": grupo_de(organization.ruc),
            "anio": DATA["anio"],
            "regimenes": [
                {"valor": r.value, "etiqueta": r.label} for r in TaxRegime
            ],
            "eventos": [
                {**e, "fecha": e["fecha"].isoformat() if e["fecha"] else None}
                for e in eventos
            ],
            "alertas": panel.alertas_financieras(organization.ruc),
            "buzon": panel.buzon_pendiente(organization.ruc),
            "suscripcion": _urls_suscripcion(request, organization),
        })

    def patch(self, request: Request) -> Response:
        regimen = str(request.data.get("regimen", "")).strip().upper()
        if regimen not in TaxRegime.values:
            return Response(
                {
                    "regimen": [
                        "Régimen inválido. Usa uno de: "
                        + ", ".join(TaxRegime.values)
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        organization = request.organization
        organization.tax_regime = regimen
        organization.save(update_fields=["tax_regime", "updated_at"])
        return Response({"regimen": regimen, "regimen_declarado": True})


class CalendarioResumenView(OrganizationAPIView):
    """Contadores para el icono del navbar. Se pide en cada carga de página,
    así que devuelve lo mínimo y nada que obligue a recorrer el cronograma
    entero."""

    def get(self, request: Request) -> Response:
        return Response(panel.resumen(request.organization, _hoy()))


class CalendarioSuscripcionRotarView(ManagedOrganizationAPIView):
    """Invalida la URL de suscripción y devuelve la nueva.

    Es el único remedio cuando esa URL se compartió por error: quien ya la
    tenga deja de recibir actualizaciones en su calendario.
    """

    def post(self, request: Request) -> Response:
        organization = request.organization
        organization.rotar_token_calendario()
        return Response(_urls_suscripcion(request, organization))


class CalendarioSuscripcionView(APIView):
    """El .ics de una empresa, identificada por su token de suscripción.

    Abierta por necesidad —los clientes de calendario no autentican— y por eso
    deliberadamente pobre: solo el cronograma, que se deriva del dígito del
    RUC. Ni alertas, ni buzón, ni importes.
    """

    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request: Request, token: str) -> HttpResponse:
        organization = get_object_or_404(Organization, calendar_token=token)
        eventos = eventos_de(organization, desde=_hoy())
        respuesta = HttpResponse(
            a_ics(organization.ruc, eventos, hoy=_hoy()),
            content_type="text/calendar; charset=utf-8",
        )
        respuesta["Content-Disposition"] = (
            f'inline; filename="calendario_{organization.ruc}.ics"'
        )
        # Los clientes de calendario reconsultan cada pocas horas; sin esto
        # algunos proxies servirían una copia vieja durante días.
        respuesta["Cache-Control"] = "no-cache, max-age=0"
        return respuesta
