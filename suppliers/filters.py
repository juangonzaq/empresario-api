"""Query filters for the suppliers API."""

from __future__ import annotations

from django_filters import rest_framework as filters

from .models import Supplier, SupplierCheck


class SupplierFilter(filters.FilterSet):
    checked_before = filters.DateTimeFilter(
        field_name="last_checked_at", lookup_expr="lt",
        label="Suppliers not checked since this moment",
    )
    never_checked = filters.BooleanFilter(
        field_name="last_checked_at", lookup_expr="isnull"
    )

    class Meta:
        model = Supplier
        fields = ("ruc", "is_tracked", "has_issue", "status", "condition")


class SupplierCheckFilter(filters.FilterSet):
    ruc = filters.CharFilter(field_name="supplier__ruc")
    date_from = filters.DateFilter(field_name="checked_on", lookup_expr="gte")
    date_to = filters.DateFilter(field_name="checked_on", lookup_expr="lte")

    class Meta:
        model = SupplierCheck
        fields = ("supplier", "checked_on", "has_issue", "changed", "succeeded")
