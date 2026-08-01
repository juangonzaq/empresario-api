"""API for the supplier registry and their daily SUNAT checks."""

from __future__ import annotations

from django.db.models import Count, Q
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from .filters import SupplierCheckFilter, SupplierFilter
from .models import Supplier, SupplierCheck
from .serializers import (
    SupplierCheckSerializer,
    SupplierDetailSerializer,
    SupplierSerializer,
)
from .services import RucLookupError, SupplierMonitor


class SupplierViewSet(viewsets.ModelViewSet):
    """Manage the supplier registry and read their SUNAT standing.

    * ``GET/POST /api/suppliers/`` — list and register
    * ``GET /api/suppliers/{uuid}/`` — supplier with its recent checks
    * ``GET /api/suppliers/{uuid}/checks/`` — full history for one supplier
    * ``POST /api/suppliers/{uuid}/check/`` — run a check right now
    * ``GET /api/suppliers/summary/`` — how many are healthy, flagged or stale
    """

    queryset = Supplier.objects.all()
    filter_backends = (
        DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter
    )
    filterset_class = SupplierFilter
    search_fields = ("ruc", "alias", "business_name", "trade_name")
    ordering_fields = ("alias", "business_name", "ruc", "last_checked_at", "has_issue")

    def get_serializer_class(self):
        if self.action == "retrieve":
            return SupplierDetailSerializer
        return SupplierSerializer

    @action(detail=True, methods=["get"])
    def checks(self, request: Request, pk=None) -> Response:
        """The full check history for one supplier, paginated."""
        queryset = self.get_object().checks.all()
        page = self.paginate_queryset(queryset)
        serializer = SupplierCheckSerializer(page or queryset, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def check(self, request: Request, pk=None) -> Response:
        """Check this supplier on SUNAT immediately, without waiting for the cron."""
        supplier = self.get_object()
        try:
            result = SupplierMonitor().check(supplier)
        except RucLookupError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY
            )
        supplier.refresh_from_db()
        return Response({
            "supplier": SupplierSerializer(supplier).data,
            "check": SupplierCheckSerializer(result).data,
        })

    @action(detail=False, methods=["get"])
    def summary(self, request: Request) -> Response:
        queryset = self.filter_queryset(self.get_queryset())
        totals = queryset.aggregate(
            total=Count("id"),
            tracked=Count("id", filter=Q(is_tracked=True)),
            with_issues=Count("id", filter=Q(has_issue=True)),
            never_checked=Count("id", filter=Q(last_checked_at__isnull=True)),
        )
        by_status = {
            row["status"] or "unknown": row["count"]
            for row in queryset.order_by().values("status").annotate(count=Count("id"))
        }
        flagged = queryset.with_issues().values(
            "ruc", "alias", "business_name", "status", "condition"
        )
        return Response({**totals, "by_status": by_status, "flagged": list(flagged)})


class SupplierCheckViewSet(viewsets.ReadOnlyModelViewSet):
    """Browse the daily check history across all suppliers."""

    queryset = SupplierCheck.objects.select_related("supplier")
    serializer_class = SupplierCheckSerializer
    filter_backends = (DjangoFilterBackend, filters.OrderingFilter)
    filterset_class = SupplierCheckFilter
    ordering_fields = ("checked_on", "status")
    ordering = ("-checked_on",)
