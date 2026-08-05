"""Read-only API for the scraped SUNAT ITF report."""

from __future__ import annotations

from django.db.models import Count, Sum
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from .filters import ItfRecordFilter
from .models import ItfRecord
from .serializers import ItfRecordSerializer


class ItfRecordViewSet(viewsets.ReadOnlyModelViewSet):
    """Browse the scraped Consulta de ITF.

    Records are written by the ``scrape_itf`` command, so the API is read-only.

    * ``GET /api/itf/records/`` — paginated list (filter by section, period range)
    * ``GET /api/itf/records/summary/`` — total base and tax per period
    """

    queryset = ItfRecord.objects.all()
    serializer_class = ItfRecordSerializer
    filter_backends = (
        DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter
    )
    filterset_class = ItfRecordFilter
    search_fields = ("declarant_ruc", "declarant_name", "operation_code")
    ordering_fields = ("period", "base_amount", "tax")
    ordering = ("-period",)

    @action(detail=False, methods=["get"])
    def summary(self, request: Request) -> Response:
        """Total ITF base and tax per period, over the filtered queryset."""
        queryset = self.filter_queryset(self.get_queryset())
        by_period = list(
            queryset.order_by()
            .values("period")
            .annotate(
                records=Count("id"),
                base_total=Sum("base_amount"),
                tax_total=Sum("tax"),
            )
            .order_by("-period")
        )
        totals = queryset.aggregate(
            base_total=Sum("base_amount"), tax_total=Sum("tax")
        )
        return Response({**totals, "by_period": by_period})
