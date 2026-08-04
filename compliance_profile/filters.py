"""Query filters for the compliance profile API."""

from __future__ import annotations

from django_filters import rest_framework as filters

from .models import ComplianceRating


class ComplianceRatingFilter(filters.FilterSet):
    """Ranges on the quarter plus the usual exact-match fields."""

    period_from = filters.NumberFilter(field_name="period", lookup_expr="gte")
    period_to = filters.NumberFilter(field_name="period", lookup_expr="lte")
    severity = filters.CharFilter(
        field_name="variables__severity", lookup_expr="iexact", distinct=True,
        label="Only ratings with at least one variable of this gravedad",
    )

    class Meta:
        model = ComplianceRating
        fields = ("taxpayer_id", "rating", "preliminary_category", "is_current", "period")
