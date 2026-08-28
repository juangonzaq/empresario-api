"""``GET /api/declaraciones/`` — lo presentado y pagado a SUNAT, por periodo."""

from __future__ import annotations

import re

from rest_framework.request import Request
from rest_framework.response import Response

from accounts.tenancy import OrganizationAPIView

from .services import (
    cruce_balance, cruce_estado_resultados, estado_del_mes, planilla_vs_plame, resumen, resumen_anual,
)

PERIODO = re.compile(r"^\d{6}$")


class DeclaracionesView(OrganizationAPIView):
    def get(self, request: Request) -> Response:
        desde = request.query_params.get("desde", "")
        if desde and not PERIODO.match(desde):
            return Response({"desde": "Usa el formato AAAAMM."}, status=400)
        return Response(resumen(request.ruc, desde=desde or None))


class RentaAnualView(OrganizationAPIView):
    def get(self, request: Request) -> Response:
        return Response({"ejercicios": resumen_anual(request.ruc)})


class CruceAnualView(OrganizationAPIView):
    """Estado de Resultados de Finanzas vs lo declarado en el 710 del ejercicio."""

    def get(self, request: Request) -> Response:
        year = request.query_params.get("year", "")
        if not re.match(r"^\d{4}$", year):
            return Response({"year": "Usa un año de cuatro dígitos."}, status=400)
        data = cruce_estado_resultados(request.ruc, int(year))
        data["balance"] = cruce_balance(request.ruc, int(year))["rows"]
        return Response(data)


class DeudaView(OrganizationAPIView):
    """El card «Deuda y pagos SUNAT» del Inicio: boletas 1662 por mes, valores
    del buzón y deuda coactiva publicada. No es el saldo de SOL."""

    def get(self, request: Request) -> Response:
        from .services.deuda import resumen_deuda

        return Response(resumen_deuda(request.ruc))


class PanoramaView(OrganizationAPIView):
    """El card del Inicio: lo del periodo que toca, presentado o no."""

    def get(self, request: Request) -> Response:
        return Response({"estado": estado_del_mes(request.ruc)})


class PlanillaHistoricoView(OrganizationAPIView):
    """El card «Equipo y planilla» del Inicio: gente por mes según PLAME,
    ficha RUC, AFPnet y planilla propia, todas a la vez."""

    def get(self, request: Request) -> Response:
        from .services.panorama import historico_planilla

        return Response(historico_planilla(request.ruc))


class PlanillaDeclaradaView(OrganizationAPIView):
    """Colaboradores: la PLAME frente a la planilla que conoce la plataforma."""

    def get(self, request: Request) -> Response:
        return Response(planilla_vs_plame(request.ruc))
