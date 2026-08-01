"""Query filters for the RUC profile API."""

from __future__ import annotations

from django_filters import rest_framework as filters

from .models import RucSnapshot


class RucSnapshotFilter(filters.FilterSet):
    captured_from = filters.DateFilter(field_name="captured_on", lookup_expr="gte")
    captured_to = filters.DateFilter(field_name="captured_on", lookup_expr="lte")

    class Meta:
        model = RucSnapshot
        fields = (
            "ruc", "captured_on", "status", "condition", "succeeded", "changed",
            "has_risk_signals", "has_coactive_debt", "has_tax_omissions",
            "has_probatory_acts", "reactiva_peru_debt", "covid_guarantee_debt",
        )
