"""Serializers for the suppliers API."""

from __future__ import annotations

from rest_framework import serializers

from .models import Supplier, SupplierCheck


class SupplierCheckSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupplierCheck
        fields = (
            "id", "checked_on", "status", "condition", "has_issue", "changed",
            "previous_status", "previous_condition", "succeeded", "error",
        )


class SupplierSerializer(serializers.ModelSerializer):
    """Suppliers are writable: this is the registry the user maintains."""

    # ruc is deliberately not redeclared: ModelSerializer derives both validate_ruc
    # and the uniqueness check from the model field, and overriding it here would
    # silently drop the latter.
    display_name = serializers.CharField(read_only=True)

    class Meta:
        model = Supplier
        fields = (
            "id", "ruc", "alias", "display_name", "is_tracked", "notes",
            "business_name", "trade_name", "taxpayer_type", "fiscal_address",
            "economic_activities", "registered_on", "started_activities_on",
            "status", "condition", "has_issue",
            "last_checked_at", "last_changed_at", "last_error",
            "created_at", "updated_at",
        )
        # Everything SUNAT owns is read-only; only the registry fields are editable.
        read_only_fields = (
            "business_name", "trade_name", "taxpayer_type", "fiscal_address",
            "economic_activities", "registered_on", "started_activities_on",
            "status", "condition", "has_issue",
            "last_checked_at", "last_changed_at", "last_error",
            "created_at", "updated_at",
        )

    def validate_ruc(self, value: str) -> str:
        return value.strip()


class SupplierDetailSerializer(SupplierSerializer):
    latest_checks = serializers.SerializerMethodField()

    class Meta(SupplierSerializer.Meta):
        fields = SupplierSerializer.Meta.fields + ("latest_checks",)

    def get_latest_checks(self, obj: Supplier) -> list[dict]:
        checks = obj.checks.all()[:30]
        return SupplierCheckSerializer(checks, many=True).data
