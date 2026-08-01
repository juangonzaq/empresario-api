"""API for REMYPE registry lookups."""

from __future__ import annotations

from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from .filters import RemypeCheckFilter
from .models import RemypeCheck
from .serializers import RemypeCheckSerializer, RemypeLookupRequestSerializer
from .services import RemypeLookupError, RemypeSynchronizer
from .services.sync import DEFAULT_MAX_AGE_DAYS


class RemypeViewSet(viewsets.ReadOnlyModelViewSet):
    """Read REMYPE standing and trigger lookups on demand.

    Accreditation barely moves, so results are cached: a stored check is reused until
    it ages past ``DEFAULT_MAX_AGE_DAYS`` unless ``force`` is passed.

    * ``GET /api/remype/`` — every recorded check
    * ``GET /api/remype/current/`` — latest check per RUC
    * ``GET /api/remype/current/?ruc=X`` — one company's current standing
    * ``GET /api/remype/me/`` — standing for the RUC this project is configured with
    * ``POST /api/remype/lookup/`` — query REMYPE now, body ``{"ruc": "...", "force": false}``
    """

    queryset = RemypeCheck.objects.all()
    serializer_class = RemypeCheckSerializer
    filter_backends = (DjangoFilterBackend, filters.OrderingFilter)
    filterset_class = RemypeCheckFilter
    ordering_fields = ("checked_on", "ruc")
    ordering = ("-checked_on", "ruc")

    def _current_for(self, ruc: str) -> RemypeCheck | None:
        return (
            RemypeCheck.objects.filter(ruc=ruc, succeeded=True)
            .order_by("-checked_on")
            .first()
        )

    def _not_stored(self, ruc: str) -> Response:
        return Response(
            {"detail": f"No REMYPE check stored for {ruc}. "
                       f"POST to /api/remype/lookup/ to run one."},
            status=status.HTTP_404_NOT_FOUND,
        )

    @action(detail=False, methods=["get"])
    def current(self, request: Request) -> Response:
        """The most recent successful check per RUC, or for ``?ruc=``."""
        ruc = request.query_params.get("ruc")
        if ruc:
            check = self._current_for(ruc)
            if check is None:
                return self._not_stored(ruc)
            return Response(self.get_serializer(check).data)

        queryset = self.filter_queryset(self.get_queryset()).filter(succeeded=True)
        # DISTINCT ON needs the same leading ORDER BY column.
        latest = list(queryset.order_by("ruc", "-checked_on").distinct("ruc"))
        page = self.paginate_queryset(latest)
        serializer = self.get_serializer(page if page is not None else latest, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def me(self, request: Request) -> Response:
        """Current standing for the project's own RUC."""
        if not settings.SUNAT_RUC:
            return Response(
                {"detail": "SUNAT_RUC is not configured."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        check = self._current_for(settings.SUNAT_RUC)
        if check is None:
            return self._not_stored(settings.SUNAT_RUC)
        return Response(self.get_serializer(check).data)

    @action(detail=False, methods=["post"])
    def lookup(self, request: Request) -> Response:
        """Query REMYPE for one RUC, reusing a recent check unless ``force`` is set."""
        serializer = RemypeLookupRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ruc = serializer.validated_data["ruc"]
        force = serializer.validated_data["force"]

        try:
            result = RemypeSynchronizer().run(
                [ruc], max_age_days=None if force else DEFAULT_MAX_AGE_DAYS
            )
        except RemypeLookupError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        check = self._current_for(ruc)
        if check is None:
            return Response(
                {"detail": f"REMYPE lookup for {ruc} did not succeed."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({
            "reused_cached_check": bool(result.skipped),
            "check": self.get_serializer(check).data,
        })
