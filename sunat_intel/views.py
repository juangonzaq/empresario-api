"""API for the SUNAT intelligence center (cases, overview, VIGÍA)."""

from __future__ import annotations

import logging

from django.db.models import Count
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, status, viewsets
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Case, CaseEvent, CaseStatus, OPEN_STATUSES, VigiaMessage
from .serializers import (
    CaseDetailSerializer, CaseListSerializer, CaseUpdateSerializer,
    VigiaMessageSerializer,
)
from accounts.tenancy import HasOrganization, OrganizationAPIView
from billing.permissions import PaidPlanActive
from rest_framework.permissions import IsAuthenticated

from .services import ask as ask_service
from .services import overview as overview_service

logger = logging.getLogger(__name__)


def default_taxpayer(request: Request) -> str:
    """El RUC de la empresa del request.

    Antes salía de ``?taxpayer_id=`` con respaldo en settings: cualquiera
    podía leer los casos de cualquier contribuyente cambiando el parámetro.
    Ahora lo pone ``HasOrganization`` a partir de las membresías de quien
    llama, y el parámetro se ignora.
    """
    return request.ruc


class CaseViewSet(

    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Cases are created by the analysis pipeline; humans update gestión
    fields (status, responsible, next action) through PATCH."""

    permission_classes = [IsAuthenticated, HasOrganization]
    http_method_names = ["get", "patch", "head", "options"]
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ("status", "risk", "requires_decision")

    def get_queryset(self):
        queryset = (
            Case.objects.filter(taxpayer_id=default_taxpayer(self.request))
            .annotate(message_count=Count("messages", distinct=True))
        )
        if self.request.query_params.get("open") == "true":
            queryset = queryset.filter(status__in=OPEN_STATUSES)
        return queryset

    def get_serializer_class(self):
        if self.action == "retrieve":
            return CaseDetailSerializer
        if self.action == "partial_update":
            return CaseUpdateSerializer
        return CaseListSerializer

    def perform_update(self, serializer):
        case: Case = serializer.instance
        actor = serializer.validated_data.pop("actor", "usuario")
        before = {
            "status": case.status,
            "responsible": case.responsible,
            "next_action": case.next_action,
        }
        updated = serializer.save()
        changes = []
        if updated.status != before["status"]:
            changes.append(
                f"estado: {before['status']} → {updated.status}"
            )
        if updated.responsible != before["responsible"]:
            changes.append(
                f"responsable: {before['responsible'] or '—'} → "
                f"{updated.responsible or '—'}"
            )
        if updated.next_action != before["next_action"]:
            changes.append("próxima acción actualizada")
        if changes:
            kind = "resolved" if updated.status == CaseStatus.RESOLVED else "updated"
            CaseEvent.objects.create(
                case=updated, actor=actor, kind=kind,
                description="; ".join(changes),
            )

    def update(self, request, *args, **kwargs):
        """PATCH responds with the full detail so the UI refreshes in place."""
        super().update(request, *args, **kwargs)
        instance = self.get_object()
        return Response(CaseDetailSerializer(instance).data)


class OverviewView(OrganizationAPIView):
    """GET /api/intel/overview/ — the executive summary and card payload."""

    def get(self, request: Request) -> Response:
        return Response(overview_service.build_overview(default_taxpayer(request)))


class VigiaHistoryView(OrganizationAPIView):
    """GET — the persisted chat history; DELETE — clear it.

    The history is also the audit trail of consultations, so DELETE only
    clears the conversation shown in the widget for this taxpayer.
    """

    MAX_MESSAGES = 100

    def get(self, request: Request) -> Response:
        messages = VigiaMessage.objects.filter(
            taxpayer_id=default_taxpayer(request)
        ).order_by("-created_at")[: self.MAX_MESSAGES]
        ordered = list(messages)[::-1]
        return Response(VigiaMessageSerializer(ordered, many=True).data)

    def delete(self, request: Request) -> Response:
        deleted, _ = VigiaMessage.objects.filter(
            taxpayer_id=default_taxpayer(request)
        ).delete()
        return Response({"deleted": deleted})


class AskView(OrganizationAPIView):
    """POST /api/intel/ask/ — grounded Q&A over the company's SUNAT data.

    The OpenAI key never leaves the backend; the frontend only sends the
    question and receives the answer with its sources.

    Vigía es del plan de pago: cada pregunta cuesta tokens reales, así que la
    prueba gratuita no lo incluye (402 con code para aterrizar en Suscripción).
    """

    permission_classes = [*OrganizationAPIView.permission_classes, PaidPlanActive]

    def post(self, request: Request) -> Response:
        question = str(request.data.get("question") or "").strip()
        if not question:
            return Response(
                {"detail": "question es requerido."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(question) > 500:
            return Response(
                {"detail": "La pregunta no puede superar 500 caracteres."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = ask_service.ask(default_taxpayer(request), question)
        except Exception:  # the user gets a friendly error; the log gets the rest
            logger.exception("VIGÍA question failed")
            return Response(
                {"detail": "No pudimos consultar al asistente. Intenta de nuevo."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(result)
