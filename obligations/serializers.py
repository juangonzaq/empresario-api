"""API shapes for the compliance module.

The backend owns statuses, scores and reasons; serializers only present them and
add time-derived fields (``time_state``, ``days_until``) computed from
``due_date`` at read time.
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework import serializers

from .models import (
    ComplianceAction, ComplianceDomain, CompanyObligation, ObligationAssessment,
    ObligationEvidence,
)
from .services.deadlines import days_until, time_state


def _user_brief(user) -> dict | None:
    if user is None:
        return None
    return {"email": user.email, "full_name": user.full_name}


class DomainSerializer(serializers.ModelSerializer):
    class Meta:
        model = ComplianceDomain
        fields = ("code", "name", "description", "sort_order")


class EvidenceSerializer(serializers.ModelSerializer):
    verified_by = serializers.SerializerMethodField()

    class Meta:
        model = ObligationEvidence
        fields = ("id", "evidence_type", "verification_status", "label", "reference",
                  "url", "valid_from", "valid_until", "notes", "verified_at",
                  "verified_by", "created_at")
        read_only_fields = ("id", "verified_at", "verified_by", "created_at")

    def get_verified_by(self, obj) -> dict | None:
        return _user_brief(obj.verified_by)


class EvidenceCreateSerializer(serializers.Serializer):
    evidence_type = serializers.ChoiceField(
        choices=ObligationEvidence._meta.get_field("evidence_type").choices, required=False,
    )
    label = serializers.CharField(max_length=200, required=False, allow_blank=True)
    url = serializers.URLField(max_length=500, required=False, allow_blank=True)
    reference = serializers.JSONField(required=False)
    valid_from = serializers.DateField(required=False, allow_null=True)
    valid_until = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(max_length=400, required=False, allow_blank=True)


class ActionSerializer(serializers.ModelSerializer):
    owner = serializers.SerializerMethodField()

    class Meta:
        model = ComplianceAction
        fields = ("id", "title", "description", "status", "priority", "owner",
                  "due_date", "completed_at", "created_at")
        read_only_fields = ("id", "completed_at", "created_at")

    def get_owner(self, obj) -> dict | None:
        return _user_brief(obj.owner)


class ActionCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(max_length=600, required=False, allow_blank=True)
    priority = serializers.ChoiceField(
        choices=ComplianceAction._meta.get_field("priority").choices, required=False,
    )
    due_date = serializers.DateField(required=False, allow_null=True)
    owner_email = serializers.EmailField(required=False, allow_blank=True)


class ActionUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=ComplianceAction._meta.get_field("status").choices, required=False,
    )
    priority = serializers.ChoiceField(
        choices=ComplianceAction._meta.get_field("priority").choices, required=False,
    )
    title = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(max_length=600, required=False, allow_blank=True)
    due_date = serializers.DateField(required=False, allow_null=True)
    owner_email = serializers.EmailField(required=False, allow_blank=True)


class AssessmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ObligationAssessment
        fields = ("id", "compliance_status", "verification_status", "evaluation_source",
                  "reason", "rule_version", "created_at")


class ObligationListSerializer(serializers.ModelSerializer):
    title = serializers.CharField(source="rule.title", read_only=True)
    code = serializers.CharField(source="rule.code", read_only=True)
    summary = serializers.CharField(source="rule.summary", read_only=True)
    obligation_type = serializers.CharField(source="rule.obligation_type", read_only=True)
    frequency = serializers.CharField(source="rule.frequency", read_only=True)
    domain = serializers.CharField(source="rule.domain.code", read_only=True)
    domain_name = serializers.CharField(source="rule.domain.name", read_only=True)
    owner = serializers.SerializerMethodField()
    time_state = serializers.SerializerMethodField()
    days_until = serializers.SerializerMethodField()
    has_evidence = serializers.SerializerMethodField()

    class Meta:
        model = CompanyObligation
        fields = (
            "id", "code", "title", "summary", "domain", "domain_name",
            "obligation_type", "frequency", "severity",
            "applicability_status", "compliance_status", "workflow_status",
            "verification_status", "due_date", "time_state", "days_until",
            "current_assessment", "owner", "has_evidence", "last_evaluated_at",
        )

    def get_owner(self, obj) -> dict | None:
        return _user_brief(obj.owner)

    def get_time_state(self, obj) -> str | None:
        return time_state(obj.due_date, timezone.localdate())

    def get_days_until(self, obj) -> int | None:
        return days_until(obj.due_date, timezone.localdate())

    def get_has_evidence(self, obj) -> bool:
        # Anotado en el queryset del list; en detalle se calcula directo.
        annotated = getattr(obj, "evidence_count", None)
        if annotated is not None:
            return annotated > 0
        return obj.evidence.exists()


class ObligationDetailSerializer(ObligationListSerializer):
    legal_reference = serializers.CharField(source="rule.legal_reference", read_only=True)
    source_name = serializers.CharField(source="rule.source_name", read_only=True)
    source_url = serializers.CharField(source="rule.source_url", read_only=True)
    remediation_steps = serializers.JSONField(source="rule.remediation_steps", read_only=True)
    domain_detail = DomainSerializer(source="rule.domain", read_only=True)
    evidence = EvidenceSerializer(many=True, read_only=True)
    actions = ActionSerializer(many=True, read_only=True)
    assessments = serializers.SerializerMethodField()

    class Meta(ObligationListSerializer.Meta):
        fields = ObligationListSerializer.Meta.fields + (
            "legal_reference", "source_name", "source_url", "remediation_steps",
            "domain_detail", "applicability_reason", "evidence", "actions", "assessments",
        )

    def get_assessments(self, obj) -> list:
        rows = obj.assessments.all()[:8]
        return AssessmentSerializer(rows, many=True).data


class ObligationUpdateSerializer(serializers.Serializer):
    """Human-owned fields only. Compliance/verification stay engine-owned."""

    workflow_status = serializers.ChoiceField(
        choices=CompanyObligation._meta.get_field("workflow_status").choices, required=False,
    )
    owner_email = serializers.EmailField(required=False, allow_blank=True)
