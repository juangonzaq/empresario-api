"""Lectura de lo guardado de AFPnet."""

from __future__ import annotations

from rest_framework import serializers

from .models import (
    AfpnetAffiliate, AfpnetContribution, AfpnetCompany, AfpnetDebt, AfpnetDeclaration,
    AfpnetPeriodSummary,
)


class AfpnetCompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = AfpnetCompany
        fields = (
            "taxpayer_id", "legal_name", "phone", "address", "department",
            "province", "district", "representative", "representative_document",
            "representative_role", "representative_email",
            "representative_phone", "second_signature", "fetched_at",
        )


class AfpnetDeclarationSerializer(serializers.ModelSerializer):
    afp_label = serializers.CharField(source="get_afp_display", read_only=True)

    class Meta:
        model = AfpnetDeclaration
        fields = (
            "id", "period", "afp", "afp_label", "presentation_number",
            "presented_at", "paid_on", "nominal_fondo", "nominal_ryr",
            "state", "worker_type", "ticket", "bank", "payment_method",
            "is_paid",
        )


class AfpnetDebtSerializer(serializers.ModelSerializer):
    afp_label = serializers.CharField(source="get_afp_display", read_only=True)

    class Meta:
        model = AfpnetDebt
        fields = ("id", "period", "afp", "afp_label", "concept", "principal",
                  "interest", "total", "state", "due_on")


class AfpnetAffiliateSerializer(serializers.ModelSerializer):
    afp_label = serializers.CharField(source="get_afp_display", read_only=True)

    class Meta:
        model = AfpnetAffiliate
        fields = ("id", "cuspp", "document_type", "document_number",
                  "full_name", "afp", "afp_label", "commission_type",
                  "state", "is_active")


class AfpnetPeriodSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = AfpnetPeriodSummary
        fields = ("period", "total_op", "op_cierta", "op_presunta",
                  "op_con_deuda", "op_sin_deuda")


class AfpnetContributionSerializer(serializers.ModelSerializer):
    situacion = serializers.CharField(read_only=True)

    class Meta:
        model = AfpnetContribution
        fields = ("period", "kind", "has_employment", "remuneration",
                  "obliged_fund", "obliged_insurance", "obliged_commission",
                  "declared_fund", "paid_fund", "declared_unpaid", "situacion")
