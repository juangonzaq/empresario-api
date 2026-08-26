"""Recibe a quien deja sus datos en la página pública."""

from __future__ import annotations

import logging

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.emails import send_email

from .models import Lead
from .serializers import LeadSerializer
from .throttles import LeadThrottle

logger = logging.getLogger(__name__)


def _notify(lead: Lead) -> None:
    """Avisa al equipo comercial si hay a quién. Nunca tumba la petición:
    el interesado ya quedó guardado, y eso es lo que importa."""
    to = getattr(settings, "LEADS_NOTIFY_EMAIL", "")
    if not to:
        return
    send_email(
        f"Nuevo interesado · {lead.name}", to, "nuevo_interesado",
        {"lead": lead, "origen": f"Origen: {lead.source}",
         "url": f"{settings.API_PUBLIC_URL or settings.FRONTEND_URL}/admin/leads/lead/{lead.pk}/change/"},
        text=(
            f"Nombre: {lead.name}\nCorreo: {lead.email}\n"
            f"Teléfono: {lead.phone or '—'}\nRUC: {lead.ruc or '—'}\n"
            f"Empresa: {lead.company or '—'}\nOrigen: {lead.source}\n\n"
            f"{lead.message or '(sin mensaje)'}\n"
        ),
    )


class LeadCreateView(APIView):
    # Sin autenticadores: es el formulario público de la landing. Una cookie
    # de sesión del admin en el mismo navegador haría que SessionAuthentication
    # exigiera CSRF y el envío muriera con «CSRF Failed».
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [LeadThrottle]

    def post(self, request: Request) -> Response:
        serializer = LeadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        lead = serializer.save()
        logger.info("Nuevo interesado: %s (%s)", lead.email, lead.source)
        _notify(lead)
        return Response(
            {"detail": "Gracias. Te escribiremos pronto."},
            status=status.HTTP_201_CREATED,
        )
