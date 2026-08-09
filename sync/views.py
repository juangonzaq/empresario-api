"""Estado de la sincronización de la empresa activa."""

from __future__ import annotations

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.tenancy import ManagedOrganizationAPIView, OrganizationAPIView

from .models import SyncJob
from .serializers import SyncJobSerializer
from .models import JobKind
from .services import NotConnected, start_sync


class SyncStatusView(OrganizationAPIView):
    """El último trabajo de la empresa. El frontend consulta esto mientras
    dura el onboarding para mostrar el avance."""

    def get(self, request: Request) -> Response:
        job = SyncJob.objects.filter(organization=request.organization).first()
        if job is None:
            return Response({"status": "sin_sincronizar", "steps": []})
        return Response(SyncJobSerializer(job).data)


class SyncStartView(ManagedOrganizationAPIView):
    """Relanza la sincronización a pedido."""

    def post(self, request: Request) -> Response:
        try:
            # Pulsar «sincronizar ahora» pide el cuadro completo.
            job = start_sync(
                request.organization,
                kind=JobKind.MANUAL,
                requested_by=request.user,
            )
        except NotConnected as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_409_CONFLICT
            )
        return Response(
            SyncJobSerializer(job).data, status=status.HTTP_202_ACCEPTED
        )
