"""El estado de la empresa activa, resumido para la barra superior.

La barra pintaba tres avisos fijos en el código —«Condición: Habido», «REMYPE
acreditado», «DJ Anual 2025 pendiente»—, iguales para cualquier empresa. Eso no
es una decoración inofensiva: son afirmaciones sobre la situación de alguien
ante SUNAT, y una empresa recién registrada las veía en verde sin que nadie
hubiera consultado nada.

Regla de este endpoint: **si no hay dato, no se afirma nada**. Cada aviso lleva
su propio ``estado``, y ``desconocido`` es un valor de primera clase que la
interfaz pinta en gris. Es la diferencia entre «estás habido» y «todavía no lo
hemos mirado», que para quien decide no es un matiz.

Va en una sola petición porque la barra está en todas las páginas: tres
llamadas por navegación para tres etiquetas sería caro sin ganar nada.
"""

from __future__ import annotations

from django.db.models import Count, Q
from rest_framework.request import Request
from rest_framework.response import Response

from .tenancy import OrganizationAPIView


def _aviso(estado: str, etiqueta: str, detalle: str = "") -> dict:
    return {"estado": estado, "etiqueta": etiqueta, "detalle": detalle}


def _condicion_ruc(ruc: str) -> dict:
    from ruc_profile.models import RucSnapshot

    snapshot = (
        RucSnapshot.objects.filter(ruc=ruc, succeeded=True)
        .order_by("-captured_on")
        .first()
    )
    if snapshot is None:
        return _aviso("desconocido", "Condición del RUC sin consultar")

    condicion = (snapshot.condition or "").strip()
    estado_ruc = (snapshot.status or "").strip()
    # Cualquier cosa que no sea HABIDO/ACTIVO se marca: los valores que SUNAT
    # pueda inventar mañana deben fallar hacia el aviso, no hacia el visto bueno.
    bien = condicion.upper() == "HABIDO" and estado_ruc.upper() == "ACTIVO"
    return _aviso(
        "ok" if bien else "atencion",
        f"Condición: {condicion.title() or 'sin dato'}",
        estado_ruc.title(),
    )


def _remype(ruc: str) -> dict:
    from remype.models import RemypeCheck

    check = (
        RemypeCheck.objects.filter(ruc=ruc, succeeded=True)
        .order_by("-checked_on")
        .first()
    )
    if check is None:
        return _aviso("desconocido", "REMYPE sin consultar")
    if check.is_registered:
        return _aviso("ok", "REMYPE acreditado", check.message or "")
    return _aviso("atencion", "Sin acreditación REMYPE", check.message or "")


def _buzon(ruc: str) -> dict:
    from sunat_mailbox.models import Message

    totales = Message.objects.filter(taxpayer_id=ruc).aggregate(
        total=Count("id"),
        urgentes=Count(
            "id",
            filter=Q(is_urgent=True, is_read=False, reviewed_at__isnull=True),
        ),
    )
    if not totales["total"]:
        return _aviso("desconocido", "Buzón sin sincronizar")
    if totales["urgentes"]:
        return _aviso(
            "atencion",
            f"{totales['urgentes']} mensajes urgentes sin revisar",
            "Buzón SUNAT",
        )
    return _aviso("ok", "Buzón al día", "Sin mensajes urgentes pendientes")


class CompanyStatusView(OrganizationAPIView):
    """``GET /api/status/`` — los avisos de la barra, para la empresa activa."""

    def get(self, request: Request) -> Response:
        ruc = request.ruc
        return Response({
            "ruc": ruc,
            "avisos": [_condicion_ruc(ruc), _remype(ruc), _buzon(ruc)],
        })
