"""Query filters for the CPE API."""

from __future__ import annotations

from django_filters import rest_framework as filters

from .models import ElectronicInvoice


class ElectronicInvoiceFilter(filters.FilterSet):
    issued_from = filters.DateFilter(field_name="issue_date", lookup_expr="gte")
    issued_to = filters.DateFilter(field_name="issue_date", lookup_expr="lte")
    period_from = filters.CharFilter(field_name="period", lookup_expr="gte")
    period_to = filters.CharFilter(field_name="period", lookup_expr="lte")
    has_xml = filters.BooleanFilter(method="filter_has_xml")

    class Meta:
        model = ElectronicInvoice
        fields = (
            "account_ruc", "direction", "document_class", "document_type",
            "series", "period", "issuer_ruc", "receiver_ruc",
            "is_cancelled", "is_rejected", "references_document",
        )

    def filter_has_xml(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.exclude(xml_content="") if value else queryset.filter(xml_content="")
