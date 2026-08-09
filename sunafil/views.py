"""Read-only API over the SUNAFIL casilla."""

from __future__ import annotations

from django.db.models import Count, Q
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from accounts.tenancy import TenantScopedViewSetMixin
from rest_framework import filters, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from .filters import SunafilItemFilter
from .models import ItemKind, SunafilItem
from .serializers import SunafilItemDetailSerializer, SunafilItemListSerializer


class SunafilItemViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Browse what SUNAFIL has deposited in the casilla.

    Items are written by the ``scrape_sunafil`` command, so the API is read-only.

    * ``GET /api/sunafil/`` — every item, filterable by ``kind``
    * ``GET /api/sunafil/{uuid}/`` — one item with the orientation body
    * ``GET /api/sunafil/orientations/`` — the invitations, newest first
    * ``GET /api/sunafil/pending/`` — unread obligations, soonest deadline first
    * ``GET /api/sunafil/summary/`` — counts per listing
    """
    tenant_field = "taxpayer_id"

    queryset = SunafilItem.objects.all()
    filter_backends = (
        DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter
    )
    filterset_class = SunafilItemFilter
    search_fields = ("subject", "record_number", "detail_text")
    ordering_fields = ("deposited_at", "due_date", "subject", "first_seen_on")
    ordering = ("-deposited_at",)

    def get_serializer_class(self):
        if self.action == "retrieve":
            return SunafilItemDetailSerializer
        return SunafilItemListSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "retrieve":
            return queryset
        # The bodies are large HTML blobs and no list view shows them.
        return queryset.defer("detail_html", "detail_text", "row")

    @action(detail=False, methods=["get"])
    def orientations(self, request: Request) -> Response:
        queryset = self.filter_queryset(self.get_queryset()).orientations()
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page if page is not None else queryset, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def pending(self, request: Request) -> Response:
        """Unread requirements and notifications, soonest deadline first."""
        queryset = (
            self.filter_queryset(self.get_queryset())
            .actionable().unread().order_by("due_date", "-deposited_at")
        )
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page if page is not None else queryset, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def summary(self, request: Request) -> Response:
        queryset = self.filter_queryset(self.get_queryset())
        today = timezone.localdate()
        totals = queryset.aggregate(
            total=Count("id"),
            unread=Count("id", filter=Q(is_read=False)),
            overdue=Count("id", filter=Q(due_date__lt=today)),
            due_soon=Count("id", filter=Q(due_date__gte=today)),
        )
        by_kind = {
            ItemKind(row["kind"]).label: row["count"]
            for row in queryset.order_by().values("kind").annotate(count=Count("id"))
        }
        return Response({**totals, "by_kind": by_kind})
