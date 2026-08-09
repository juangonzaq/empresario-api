"""Read-only API for the scraped SUNAT compliance profile."""

from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from accounts.tenancy import TenantScopedViewSetMixin
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .filters import ComplianceRatingFilter
from .models import ComplianceRating
from .serializers import (
    ComplianceRatingDetailSerializer,
    ComplianceRatingListSerializer,
)
from .services import findings as findings_service


NO_DATA_RESPONSE = {
    "detail": "No compliance rating stored yet. "
              "Run manage.py scrape_compliance_profile first."
}


class ComplianceRatingViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Browse the scraped perfil de cumplimiento.

    Ratings are written by the ``scrape_compliance_profile`` command, so the API
    is read-only.

    * ``GET /api/compliance/ratings/`` — every quarter, newest first
    * ``GET /api/compliance/ratings/{id}/`` — one quarter with its variables
    * ``GET /api/compliance/ratings/current/`` — the quarter SUNAT reports as vigente
    * ``GET /api/compliance/ratings/latest/`` — the most recent stored quarter
    """
    tenant_field = "taxpayer_id"

    queryset = ComplianceRating.objects.all()
    filter_backends = (
        DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter
    )
    filterset_class = ComplianceRatingFilter
    search_fields = ("variables__description", "variables__code")
    ordering_fields = ("period", "loaded_at", "rating")
    ordering = ("-period",)

    def get_serializer_class(self):
        if self.action in ("retrieve", "current", "latest"):
            return ComplianceRatingDetailSerializer
        return ComplianceRatingListSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "list":
            # The list serializer never touches variables or the raw payloads, so
            # keep the (potentially large) JSON columns out of the query.
            return queryset.defer("header_payload", "detail_payload")
        return queryset.prefetch_related("variables")

    @action(detail=False, methods=["get"])
    def current(self, request: Request) -> Response:
        """The vigente calificación (optionally scoped with ``?taxpayer_id=``)."""
        rating = self.filter_queryset(self.get_queryset()).current().first()
        if rating is None:
            return Response(NO_DATA_RESPONSE, status=status.HTTP_404_NOT_FOUND)
        return Response(self.get_serializer(rating).data)

    @action(detail=False, methods=["get"])
    def latest(self, request: Request) -> Response:
        """The most recent rating by period (optionally scoped with ``?taxpayer_id=``).

        Unlike ``current``, this ignores SUNAT's vigente flag and simply returns
        the newest quarter stored, breaking ties by scrape time.
        """
        rating = (
            self.filter_queryset(self.get_queryset())
            .order_by("-period", "-execution_period", "-loaded_at")
            .first()
        )
        if rating is None:
            return Response(NO_DATA_RESPONSE, status=status.HTTP_404_NOT_FOUND)
        return Response(self.get_serializer(rating).data)


class BaseFindingsView(APIView):
    """Shared lookup: the newest stored evaluation, optionally by taxpayer."""

    def get_rating(self, request: Request) -> ComplianceRating | None:
        queryset = ComplianceRating.objects.all()
        taxpayer_id = request.query_params.get("taxpayer_id")
        if taxpayer_id:
            queryset = queryset.for_taxpayer(taxpayer_id)
        return (
            queryset.order_by("-period", "-execution_period", "-loaded_at")
            .prefetch_related("variables")
            .first()
        )

    def get_previous(self, rating: ComplianceRating) -> ComplianceRating | None:
        return (
            ComplianceRating.objects.for_taxpayer(rating.taxpayer_id)
            .exclude(pk=rating.pk)
            .exclude(rating="")
            .order_by("-period", "-execution_period", "-loaded_at")
            .first()
        )


class ComplianceSummaryView(BaseFindingsView):
    """``GET /api/compliance/summary/`` — the executive card payload.

    Groups variables by code (no repeated codes counted twice), never computes
    a 0–100 score, never sums amounts into a "current debt", and only returns a
    trend when a previous evaluation is actually stored.
    """

    def get(self, request: Request) -> Response:
        rating = self.get_rating(request)
        if rating is None:
            return Response(NO_DATA_RESPONSE, status=status.HTTP_404_NOT_FOUND)
        return Response(
            findings_service.build_summary(rating, self.get_previous(rating))
        )


class ComplianceFindingsView(BaseFindingsView):
    """``GET /api/compliance/findings/`` — normalised findings list."""

    def get(self, request: Request) -> Response:
        rating = self.get_rating(request)
        if rating is None:
            return Response(NO_DATA_RESPONSE, status=status.HTTP_404_NOT_FOUND)
        return Response(findings_service.build_findings_list(rating))


class ComplianceFindingDetailView(BaseFindingsView):
    """``GET /api/compliance/findings/{code}/`` — one finding with its events."""

    def get(self, request: Request, code: str) -> Response:
        rating = self.get_rating(request)
        if rating is None:
            return Response(NO_DATA_RESPONSE, status=status.HTTP_404_NOT_FOUND)
        payload = findings_service.build_finding_detail(rating, code)
        if payload is None:
            return Response(
                {"detail": f"No finding with code {code!r} in the latest evaluation."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(payload)
