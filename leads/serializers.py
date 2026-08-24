from __future__ import annotations

from rest_framework import serializers

from .models import Lead


class LeadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lead
        fields = ("name", "email", "phone", "ruc", "company", "message", "source")
        extra_kwargs = {"source": {"required": False}}

    def validate_name(self, value: str) -> str:
        name = value.strip()
        if len(name) < 2:
            raise serializers.ValidationError("Cuéntanos tu nombre.")
        return name

    def validate_email(self, value: str) -> str:
        return value.strip().lower()

    def validate_ruc(self, value: str) -> str:
        return value.strip()
