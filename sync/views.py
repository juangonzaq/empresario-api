"""Estado de la sincronización de la empresa activa."""

from __future__ import annotations

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.tenancy import ManagedOrganizationAPIView, OrganizationAPIView

from .models import JobKind, SyncJob
from .serializers import SyncJobSerializer
from .services import (
    CannotRetry, NotConnected, retry_step, run_source, start_sync,
)


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


class SyncStepRetryView(ManagedOrganizationAPIView):
    """Relanza un solo paso del último trabajo.

    Cuando una fuente se cae —el portal no respondió, la clave estaba mal—
    repetir la sincronización entera para arreglar ese paso obliga a rehacer
    los que sí funcionaron, y el histórico de comprobantes tarda minutos.
    """

    def post(self, request: Request, key: str) -> Response:
        try:
            job = retry_step(request.organization, key, requested_by=request.user)
        except CannotRetry as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(
            SyncJobSerializer(job).data, status=status.HTTP_202_ACCEPTED
        )


class SyncSourceRunView(ManagedOrganizationAPIView):
    """Vuelve a traer UNA fuente a pedido, haya ido bien o mal la última vez.

    Es lo que necesita el botón «sincronizar» de cada sección: preguntar por lo
    último de SUNAFIL tiene sentido justo cuando la sincronización anterior
    terminó bien, que es cuando el reintento por paso se niega a correr.
    """

    def post(self, request: Request, key: str) -> Response:
        try:
            job = run_source(request.organization, key, requested_by=request.user)
        except CannotRetry as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(
            SyncJobSerializer(job).data, status=status.HTTP_202_ACCEPTED
        )
