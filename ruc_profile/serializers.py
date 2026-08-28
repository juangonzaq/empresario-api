"""Serializers for the RUC profile API."""

from __future__ import annotations

from rest_framework import serializers

from suppliers.validators import validate_ruc

from .models import LegalRepresentative, RucSection, RucSnapshot, WorkerHeadcount


class RucSectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = RucSection
        fields = ("key", "label", "title", "has_data", "answer", "tables", "error")


class LegalRepresentativeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LegalRepresentative
        fields = ("id", "document_type", "document_number", "full_name", "role", "since")


class WorkerHeadcountSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkerHeadcount
        fields = ("id", "period", "workers", "pensioners", "service_providers")


class RucSnapshotListSerializer(serializers.ModelSerializer):
    """Compact view: identity plus the risk flags."""

    class Meta:
        model = RucSnapshot
        fields = (
            "id", "ruc", "captured_on", "business_name", "trade_name",
            "taxpayer_type", "status", "condition",
            "has_coactive_debt", "has_tax_omissions", "has_probatory_acts",
            "reactiva_peru_debt", "covid_guarantee_debt", "has_risk_signals",
            "worker_count", "latest_worker_period", "branch_count",
            "changed", "change_summary", "succeeded", "error",
        )


class RucSnapshotDetailSerializer(RucSnapshotListSerializer):
    """Everything, including each button's parsed tables."""

    sections = RucSectionSerializer(many=True, read_only=True)
    legal_representatives = LegalRepresentativeSerializer(many=True, read_only=True)
    headcounts = WorkerHeadcountSerializer(many=True, read_only=True)

    class Meta(RucSnapshotListSerializer.Meta):
        fields = RucSnapshotListSerializer.Meta.fields + (
            "fiscal_address", "economic_activities", "electronic_invoicing",
            "registries", "registered_on", "started_activities_on",
            "sections", "legal_representatives", "headcounts",
            "created_at", "updated_at",
        )


class CaptureRequestSerializer(serializers.Serializer):
    """Body of an on-demand capture."""

    ruc = serializers.CharField(max_length=11, validators=[validate_ruc])
    force = serializers.BooleanField(
        default=False, help_text="Capture again even if a recent snapshot exists."
    )

    def validate_ruc(self, value: str) -> str:
        return value.strip()
