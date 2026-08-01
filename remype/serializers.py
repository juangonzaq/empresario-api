"""Serializers for the REMYPE API."""

from __future__ import annotations

from rest_framework import serializers

from suppliers.validators import validate_ruc

from .models import RemypeCheck


class RemypeCheckSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = RemypeCheck
        fields = (
            "id", "ruc", "checked_on", "is_registered", "is_active",
            "business_name", "condition", "situation", "mype_category",
            "file_number", "registry_code",
            "requested_on", "accredited_on", "deregistered_on",
            "changed", "previous_condition", "succeeded", "message",
            "created_at",
        )


class RemypeLookupRequestSerializer(serializers.Serializer):
    """Body of an on-demand lookup."""

    ruc = serializers.CharField(max_length=11, validators=[validate_ruc])
    force = serializers.BooleanField(
        default=False,
        help_text="Ignore the cached check and query REMYPE again.",
    )

    def validate_ruc(self, value: str) -> str:
        return value.strip()
