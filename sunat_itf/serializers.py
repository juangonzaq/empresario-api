"""Serializers for the read-only ITF API."""

from __future__ import annotations

from rest_framework import serializers

from .models import ItfRecord


class ItfRecordSerializer(serializers.ModelSerializer):
    section_label = serializers.CharField(source="get_section_display", read_only=True)

    class Meta:
        model = ItfRecord
        fields = (
            "id", "taxpayer_id", "section", "section_label", "period",
            "declarant_ruc", "declarant_name", "doc_type",
            "kind", "movement", "modality", "operation_code",
            "base_amount", "tax", "extra",
        )
