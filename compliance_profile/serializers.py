"""Serializers for the read-only compliance profile API."""

from __future__ import annotations

from rest_framework import serializers

from .models import ComplianceRating, ComplianceVariable


class ComplianceVariableSerializer(serializers.ModelSerializer):
    variable_type_label = serializers.CharField(
        source="get_variable_type_display", read_only=True
    )

    class Meta:
        model = ComplianceVariable
        fields = (
            "id", "variable_type", "variable_type_label", "type_label", "code",
            "description", "severity", "entity_name", "record_count",
            "field_metadata", "records", "observation",
            "is_complete", "is_multipage",
        )


class ComplianceRatingListSerializer(serializers.ModelSerializer):
    """Compact representation used for list endpoints."""

    rating_label = serializers.CharField(source="get_rating_display", read_only=True)
    has_detail = serializers.SerializerMethodField()

    class Meta:
        model = ComplianceRating
        fields = (
            "id", "taxpayer_id", "period", "rating", "rating_label",
            "preliminary_category", "execution_period",
            "evaluation_start", "evaluation_end", "loaded_at",
            "is_current", "has_detail", "detail_fetched_at",
        )

    def get_has_detail(self, obj: ComplianceRating) -> bool:
        return obj.detail_fetched_at is not None


class ComplianceRatingDetailSerializer(ComplianceRatingListSerializer):
    """Full representation, including variables and the raw SUNAT payloads."""

    variables = ComplianceVariableSerializer(many=True, read_only=True)

    class Meta(ComplianceRatingListSerializer.Meta):
        fields = ComplianceRatingListSerializer.Meta.fields + (
            "variables", "data_location_code",
            "header_payload", "detail_payload",
            "created_at", "updated_at",
        )
