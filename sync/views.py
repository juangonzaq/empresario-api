"""Estado de la sincronización de la empresa activa."""

from __future__ import annotations

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.tenancy import ManagedOrganizationAPIView, OrganizationAPIView

from .models import JobKind, SyncJob
from .serializers import SyncJobHistorySerializer, SyncJobSerializer
from .services import (
    CannotRetry, NotConnected, SyncLimitReached, manual_quota, retry_step,
    run_source, start_manual_sync,
)
from .sources import SOURCES


class SyncStatusView(OrganizationAPIView):
    """El último trabajo de la empresa. El frontend consulta esto mientras
    dura el onboarding para mostrar el avance."""

    def get(self, request: Request) -> Response:
        job = SyncJob.objects.filter(organization=request.organization).first()
        if job is None:
            return Response({"status": "sin_sincronizar", "steps": []})
        return Response(SyncJobSerializer(job).data)


class SyncStartView(ManagedOrganizationAPIView):
    """Relanza la sincronización a pedido, respetando el tope diario.

    ``only`` (lista de claves de fuente) limita qué se trae; vacío = todo.
    Pasado el tope diario responde 402 con la cuota; con ``accept_charge`` se
    continúa y se registra el cargo. La respuesta lleva ``charged`` y ``quota``."""

    def post(self, request: Request) -> Response:
        only = request.data.get("only") or None
        if only is not None and not isinstance(only, list):
            only = None
        accept_charge = bool(request.data.get("accept_charge"))
        try:
            job, charged, quota = start_manual_sync(
                request.organization, requested_by=request.user,
                only=only, accept_charge=accept_charge,
            )
        except SyncLimitReached as exc:
            # 409, no 402: el 402 lo intercepta el front como «suscripción
            # vencida». El front distingue este caso por ``code``.
            return Response(
                {"code": "sync_charge_required",
                 "detail": (f"Ya usaste tus {exc.quota['limit']} sincronizaciones manuales de hoy. "
                            f"Una más cuesta S/ {exc.quota['price']}."),
                 "quota": exc.quota},
                status=status.HTTP_409_CONFLICT,
            )
        except NotConnected as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(
            {**SyncJobSerializer(job).data, "charged": charged, "quota": quota},
            status=status.HTTP_202_ACCEPTED,
        )


class SyncHistoryView(OrganizationAPIView):
    """Lo que alimenta el panel de «Traer comprobantes»: la cuota manual de hoy,
    el catálogo de fuentes (checklist) y las últimas sincronizaciones —manuales
    y automáticas— con sus fallas."""

    def get(self, request: Request) -> Response:
        jobs = list(SyncJob.objects.filter(organization=request.organization)[:15])
        return Response({
            "quota": manual_quota(request.organization),
            "sources": [
                {"key": s.key, "label": s.label, "needs_sol": s.needs_sol}
                for s in SOURCES
            ],
            "jobs": SyncJobHistorySerializer(jobs, many=True).data,
        })


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
