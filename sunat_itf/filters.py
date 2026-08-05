"""Query filters for the ITF API."""

from __future__ import annotations

from django_filters import rest_framework as filters

from .models import ItfRecord


class ItfRecordFilter(filters.FilterSet):
    period_from = filters.CharFilter(field_name="period", lookup_expr="gte")
    period_to = filters.CharFilter(field_name="period", lookup_expr="lte")

    class Meta:
        model = ItfRecord
        fields = ("taxpayer_id", "section", "period", "declarant_ruc")
