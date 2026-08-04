"""Read-only API for the scraped SUNAT compliance profile."""

from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from .filters import ComplianceRatingFilter
from .models import ComplianceRating
from .serializers import (
    ComplianceRatingDetailSerializer,
    ComplianceRatingListSerializer,
)


class ComplianceRatingViewSet(viewsets.ReadOnlyModelViewSet):
    """Browse the scraped perfil de cumplimiento.

    Ratings are written by the ``scrape_compliance_profile`` command, so the API
    is read-only.

    * ``GET /api/compliance/ratings/`` — every quarter, newest first
    * ``GET /api/compliance/ratings/{id}/`` — one quarter with its variables
    * ``GET /api/compliance/ratings/current/`` — the quarter SUNAT reports as vigente
    """

    queryset = ComplianceRating.objects.all()
    filter_backends = (
        DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter
    )
    filterset_class = ComplianceRatingFilter
    search_fields = ("variables__description", "variables__code")
    ordering_fields = ("period", "loaded_at", "rating")
    ordering = ("-period",)

    def get_serializer_class(self):
        if self.action in ("retrieve", "current"):
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
            return Response(
                {"detail": "No compliance rating stored yet. "
                           "Run manage.py scrape_compliance_profile first."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(self.get_serializer(rating).data)
