"""API for the full SUNAT RUC profile."""

from __future__ import annotations

from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
from accounts.tenancy import HasOrganization, visible_rucs
from rest_framework.permissions import IsAuthenticated
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from suppliers.services.ruc_client import RucLookupError

from .filters import RucSnapshotFilter
from .models import RucSnapshot
from .serializers import (
    CaptureRequestSerializer,
    RucSnapshotDetailSerializer,
    RucSnapshotListSerializer,
)
from .services import RucProfileSynchronizer
from .services.sync import DEFAULT_MAX_AGE_DAYS


class RucProfileViewSet(viewsets.ReadOnlyModelViewSet):
    """Browse captured RUC profiles and capture new ones.

    * ``GET /api/ruc-profiles/`` — every snapshot
    * ``GET /api/ruc-profiles/{uuid}/`` — one snapshot with all sections
    * ``GET /api/ruc-profiles/current/`` — latest snapshot per RUC
    * ``GET /api/ruc-profiles/current/?ruc=X`` — one company's latest, in full
    * ``GET /api/ruc-profiles/me/`` — latest for the configured RUC
    * ``POST /api/ruc-profiles/capture/`` — capture now, ``{"ruc": "...", "force": false}``
    """
    permission_classes = [IsAuthenticated, HasOrganization]

    def get_queryset(self):
        # La ficha de un RUC es información pública, pero *a quién
        # consulta* una empresa no lo es: solo se devuelven su propio
        # RUC y los de sus proveedores.
        return super().get_queryset().filter(ruc__in=visible_rucs(self.request))


    queryset = RucSnapshot.objects.all()
    filter_backends = (DjangoFilterBackend, filters.OrderingFilter)
    filterset_class = RucSnapshotFilter
    ordering_fields = ("captured_on", "ruc")
    ordering = ("-captured_on", "ruc")

    def get_serializer_class(self):
        if self.action in ("retrieve", "me") or (
            self.action == "current" and self.request.query_params.get("ruc")
        ):
            return RucSnapshotDetailSerializer
        return RucSnapshotListSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action in ("retrieve", "me", "current"):
            return queryset.prefetch_related(
                "sections", "legal_representatives", "headcounts"
            )
        return queryset

    def _latest_for(self, ruc: str) -> RucSnapshot | None:
        return (
            self.get_queryset().filter(ruc=ruc, succeeded=True)
            .order_by("-captured_on").first()
        )

    def _not_captured(self, ruc: str) -> Response:
        return Response(
            {"detail": f"No profile captured for {ruc}. "
                       f"POST to /api/ruc-profiles/capture/ to run one."},
            status=status.HTTP_404_NOT_FOUND,
        )

    @action(detail=False, methods=["get"])
    def current(self, request: Request) -> Response:
        ruc = request.query_params.get("ruc")
        if ruc:
            snapshot = self._latest_for(ruc)
            if snapshot is None:
                return self._not_captured(ruc)
            return Response(self.get_serializer(snapshot).data)

        queryset = self.filter_queryset(self.get_queryset()).filter(succeeded=True)
        latest = list(queryset.order_by("ruc", "-captured_on").distinct("ruc"))
        page = self.paginate_queryset(latest)
        serializer = self.get_serializer(page if page is not None else latest, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def me(self, request: Request) -> Response:
        """Full profile for the RUC this project is configured with."""
        if not settings.SUNAT_RUC:
            return Response(
                {"detail": "SUNAT_RUC is not configured."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        snapshot = self._latest_for(settings.SUNAT_RUC)
        if snapshot is None:
            return self._not_captured(settings.SUNAT_RUC)
        return Response(self.get_serializer(snapshot).data)

    @action(detail=False, methods=["post"])
    def capture(self, request: Request) -> Response:
        serializer = CaptureRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ruc = serializer.validated_data["ruc"]
        force = serializer.validated_data["force"]

        try:
            result = RucProfileSynchronizer().run(
                [ruc], max_age_days=None if force else DEFAULT_MAX_AGE_DAYS
            )
        except RucLookupError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        snapshot = self._latest_for(ruc)
        if snapshot is None:
            return Response(
                {"detail": f"Profile capture for {ruc} did not succeed."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({
            "reused_recent_snapshot": bool(result.skipped),
            "snapshot": RucSnapshotDetailSerializer(snapshot).data,
        })
