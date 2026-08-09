"""Read-only API for the scraped emitted electronic comprobantes."""

from __future__ import annotations

from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from accounts.tenancy import TenantScopedViewSetMixin
from rest_framework import filters, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response

from .filters import ElectronicInvoiceFilter
from .models import ElectronicInvoice
from .serializers import (
    ElectronicInvoiceDetailSerializer,
    ElectronicInvoiceListSerializer,
)


class ElectronicInvoiceViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Browse the scraped emitted comprobantes and their XML.

    Records are written by the ``scrape_cpe`` command, so the API is read-only.

    * ``GET /api/cpe/invoices/`` — paginated list (filter by period/date/receiver)
    * ``GET /api/cpe/invoices/{id}/`` — full record including the XML text
    * ``GET /api/cpe/invoices/{id}/xml/`` — the raw XML as a download
    * ``GET /api/cpe/invoices/summary/`` — count and total per period
    """
    tenant_field = "account_ruc"

    queryset = ElectronicInvoice.objects.all()
    filter_backends = (
        DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter
    )
    filterset_class = ElectronicInvoiceFilter
    search_fields = ("full_number", "series", "number", "receiver_ruc", "receiver_name")
    ordering_fields = ("issue_date", "number", "total_amount", "period")
    ordering = ("-issue_date", "-number")

    def get_serializer_class(self):
        if self.action == "retrieve":
            return ElectronicInvoiceDetailSerializer
        return ElectronicInvoiceListSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "list":
            # The list view never needs the XML text or raw payload.
            return queryset.defer("xml_content", "raw")
        return queryset

    @action(detail=True, methods=["get"])
    def xml(self, request: Request, pk: str | None = None) -> HttpResponse:
        """Return the stored signed XML as a file download."""
        invoice = self.get_object()
        if not invoice.xml_content:
            raise NotFound("This comprobante has no XML stored yet.")
        filename = invoice.xml_filename or f"{invoice.series}-{invoice.number}.xml"
        response = HttpResponse(
            invoice.xml_content, content_type="application/xml; charset=ISO-8859-1"
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    @action(detail=False, methods=["get"])
    def summary(self, request: Request) -> Response:
        """Counts and totals per period and per direction, over the filtered set."""
        queryset = self.filter_queryset(self.get_queryset())
        by_period = list(
            queryset.order_by()
            .values("period", "direction", "document_class")
            .annotate(count=Count("id"), total=Sum("total_amount"))
            .order_by("-period")
        )
        totals = queryset.aggregate(
            count=Count("id"),
            total=Sum("total_amount"),
            with_xml=Count("id", filter=~Q(xml_content="")),
            issued=Count("id", filter=Q(direction="emitida")),
            received=Count("id", filter=Q(direction="recibida")),
        )
        return Response({**totals, "by_period": by_period})
