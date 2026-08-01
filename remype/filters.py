"""Query filters for the REMYPE API."""

from __future__ import annotations

from django_filters import rest_framework as filters

from .models import RemypeCheck


class RemypeCheckFilter(filters.FilterSet):
    date_from = filters.DateFilter(field_name="checked_on", lookup_expr="gte")
    date_to = filters.DateFilter(field_name="checked_on", lookup_expr="lte")
    is_active = filters.BooleanFilter(
        method="filter_is_active", label="Registered and not struck off"
    )

    class Meta:
        model = RemypeCheck
        fields = ("ruc", "checked_on", "is_registered", "changed", "succeeded")

    def filter_is_active(self, queryset, name, value):
        if value is None:
            return queryset
        active = {"is_registered": True, "deregistered_on__isnull": True}
        return queryset.filter(**active) if value else queryset.exclude(**active)
