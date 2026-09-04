"""API de descargas masivas: pedir el código y bajar el .zip con él.

Solo con plan de pago (``PaidPlanActive``): es una función de la versión de
pago, como las de IA. El código va al correo de quien pide, así que ni un
token de sesión filtrado ni otro miembro de la empresa pueden bajar el
archivo por él.
"""

from __future__ import annotations

from django.db import transaction
from django.http import HttpResponse
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.tenancy import OrganizationAPIView
from accounts.throttles import CorreoThrottle
from billing.permissions import PaidPlanActive
from core.emails import send_email

from .models import CODE_MINUTES, DocumentExport
from .services.bundle import (
    MAX_DOCUMENTS, InvalidSpec, build_zip, parse_spec, select_documents, zip_name,
)


def _ofuscado(correo: str) -> str:
    usuario, _, dominio = correo.partition("@")
    return f"{usuario[:2]}***@{dominio}"


class ExportRequestView(OrganizationAPIView):
    """``POST /api/documents/exports/`` — valida el filtro, cuenta los
    comprobantes y manda el código al correo. Devuelve el id de la solicitud
    y cuántos documentos entran, para que la pantalla lo diga antes de pedir
    el código."""

    permission_classes = [*OrganizationAPIView.permission_classes, PaidPlanActive]
    # Cada solicitud es un correo: mismo freno que la recuperación de clave.
    throttle_classes = [CorreoThrottle]

    def post(self, request: Request) -> Response:
        try:
            spec = parse_spec(request.data)
        except InvalidSpec as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        count = select_documents(request.ruc, spec).count()
        if count == 0:
            return Response(
                {"detail": "No hay comprobantes con ese filtro: no hay nada que descargar."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if count > MAX_DOCUMENTS:
            return Response(
                {"detail": f"Son {count:,} comprobantes y el máximo por descarga es "
                           f"{MAX_DOCUMENTS:,}. Acota el rango de meses.".replace(",", " ")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        label = spec.label()
        export, code = DocumentExport.issue(
            account_ruc=request.ruc, user=request.user, source=spec.source,
            filters=spec.as_filters(), label=label, document_count=count,
        )
        empresa = request.organization.name or request.ruc
        send_email(
            subject="Tu código para descargar comprobantes",
            to=request.user.email,
            template="exportacion_otp",
            context={
                "codigo": code, "minutos": CODE_MINUTES, "etiqueta": label,
                "empresa": empresa, "cantidad": count,
            },
            text=(
                f"Pediste descargar {label} de {empresa} ({count} comprobantes) en un .zip. "
                f"Tu código es {code}. Vence en {CODE_MINUTES} minutos y sirve una sola vez. "
                "Si no fuiste tú, cambia tu contraseña de inmediato."
            ),
        )
        return Response({
            "id": str(export.pk),
            "label": label,
            "document_count": count,
            "email": _ofuscado(request.user.email),
            "expires_in_minutes": CODE_MINUTES,
        }, status=status.HTTP_202_ACCEPTED)


class ExportDownloadView(OrganizationAPIView):
    """``POST /api/documents/exports/{id}/download/`` con ``{"code"}`` —
    devuelve el .zip si el código es el que se mandó, sigue vigente y no se
    ha usado. Cinco fallos cierran la solicitud: hay que pedir otro código."""

    permission_classes = [*OrganizationAPIView.permission_classes, PaidPlanActive]

    def post(self, request: Request, pk) -> HttpResponse | Response:
        code = str(request.data.get("code") or "").strip()
        with transaction.atomic():
            export = (
                DocumentExport.objects.select_for_update()
                .filter(pk=pk, account_ruc=request.ruc, user=request.user).first()
            )
            if export is None:
                return Response(status=status.HTTP_404_NOT_FOUND)
            if not export.is_usable:
                return Response(
                    {"detail": "El código venció o ya se usó. Pide uno nuevo.",
                     "code": "codigo_vencido"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not export.code_matches(code):
                export.register_failed_attempt()
                left = export.attempts_left
                detail = (
                    f"Código incorrecto. Te quedan {left} intentos." if left > 1
                    else "Código incorrecto. Te queda un intento." if left == 1
                    else "Demasiados intentos: pide un código nuevo."
                )
                return Response(
                    {"detail": detail, "code": "codigo_incorrecto", "attempts_left": left},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            content = build_zip(export, request.organization.name or "")
            export.consume(len(content))
        response = HttpResponse(content, content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="{zip_name(export)}"'
        return response
