"""Query filters for the SUNAFIL API."""

from __future__ import annotations

from django.db.models import Q
from django.utils import timezone
from django_filters import rest_framework as filters

from .models import ItemKind, SunafilItem


class SunafilItemFilter(filters.FilterSet):
    deposited_from = filters.DateTimeFilter(field_name="deposited_at", lookup_expr="gte")
    deposited_to = filters.DateTimeFilter(field_name="deposited_at", lookup_expr="lte")
    due_before = filters.DateFilter(field_name="due_date", lookup_expr="lte")
    overdue = filters.BooleanFilter(method="filter_overdue")
    actionable = filters.BooleanFilter(
        method="filter_actionable",
        label="Only items carrying an obligation (excludes orientations)",
    )

    class Meta:
        model = SunafilItem
        fields = ("taxpayer_id", "kind", "is_read", "status", "record_number")

    def filter_overdue(self, queryset, name, value):
        if value is None:
            return queryset
        past_due = Q(due_date__lt=timezone.localdate())
        return queryset.filter(past_due) if value else queryset.exclude(past_due)

    def filter_actionable(self, queryset, name, value):
        if value is None:
            return queryset
        is_orientation = Q(kind=ItemKind.ORIENTATION)
        return queryset.exclude(is_orientation) if value else queryset.filter(is_orientation)
