"""Serializers for the SUNAFIL API."""

from __future__ import annotations

from rest_framework import serializers

from .models import SunafilItem


class SunafilItemListSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    has_detail = serializers.BooleanField(read_only=True)

    class Meta:
        model = SunafilItem
        fields = (
            "id", "taxpayer_id", "kind", "kind_label", "subject", "category",
            "record_number", "office", "status", "is_read",
            "deposited_at", "acknowledged_at", "notified_on",
            "due_date", "deadline_days", "has_detail",
            "first_seen_on", "last_seen_on",
        )


class SunafilItemDetailSerializer(SunafilItemListSerializer):
    """Adds the orientation body and the raw listing row."""

    class Meta(SunafilItemListSerializer.Meta):
        fields = SunafilItemListSerializer.Meta.fields + (
            "detail_text", "detail_html", "detail_links", "detail_images",
            "detail_fetched_at", "row", "created_at", "updated_at",
        )
