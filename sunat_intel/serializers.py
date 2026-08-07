"""Serializers for the SUNAT intelligence API."""

from __future__ import annotations

from rest_framework import serializers

from .models import Case, CaseEvent, CaseStatus, MessageAnalysis, VigiaMessage


class VigiaMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = VigiaMessage
        fields = ("id", "role", "content", "sources", "has_sufficient_info", "created_at")


class CaseEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = CaseEvent
        fields = ("id", "actor", "kind", "description", "created_at")


class RelatedMessageSerializer(serializers.Serializer):
    """Compact view of a case's messages, including each one's analysis."""

    def to_representation(self, message):
        analysis: MessageAnalysis | None = getattr(message, "analysis", None)
        return {
            "id": str(message.id),
            "subject": message.subject,
            "published_at": message.published_at,
            "comm_type": analysis.comm_type if analysis else "",
            "priority": analysis.priority if analysis else None,
            "summary": analysis.summary if analysis else "",
            "sources": analysis.sources if analysis else [],
            "missing_info": analysis.missing_info if analysis else [],
            "confidence": analysis.confidence if analysis else None,
        }


class CaseListSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    risk_label = serializers.CharField(source="get_risk_display", read_only=True)
    message_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Case
        fields = (
            "id", "title", "summary", "risk", "risk_label", "status",
            "status_label", "requires_decision", "responsible", "next_action",
            "exposure_amount", "exposure_source", "deadline", "deadline_source",
            "tribute", "tax_period", "confidence", "message_count", "updated_at",
        )


class CaseDetailSerializer(CaseListSerializer):
    messages = RelatedMessageSerializer(many=True, read_only=True)
    events = CaseEventSerializer(many=True, read_only=True)

    class Meta(CaseListSerializer.Meta):
        fields = CaseListSerializer.Meta.fields + (
            "why_it_matters", "group_key", "messages", "events", "created_at",
        )


class CaseUpdateSerializer(serializers.ModelSerializer):
    """Human-editable fields only; the AI never writes through this path."""

    actor = serializers.CharField(
        write_only=True, required=False, default="usuario", max_length=120
    )

    class Meta:
        model = Case
        fields = ("status", "responsible", "next_action", "actor")
        extra_kwargs = {
            "status": {"required": False},
            "responsible": {"required": False},
            "next_action": {"required": False},
        }

    def validate_status(self, value):
        if value not in CaseStatus.values:
            raise serializers.ValidationError("Estado no válido.")
        return value
