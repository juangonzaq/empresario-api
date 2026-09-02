from __future__ import annotations

from rest_framework import serializers

from .models import BankMovement, DocumentReconciliation, MovementCategory


class DocumentReconciliationSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentReconciliation
        fields = ("id", "direction", "doc_key", "counterparty_ruc", "counterparty_name",
                  "status", "level", "cpe_total", "sire_total", "cpe_igv", "sire_igv", "differences")


class BankMovementSerializer(serializers.ModelSerializer):
    # Quién clasificó y cuándo: la columna de evidencia lo muestra completo.
    classified_by_email = serializers.SerializerMethodField()

    class Meta:
        model = BankMovement
        fields = ("id", "date", "period", "bank", "bank_account", "currency", "kind", "amount",
                  "description", "operation_number", "source", "category", "confidence",
                  "evidence", "classified_by", "classified_by_email", "classified_at")
        read_only_fields = ("id", "period", "confidence", "evidence", "classified_by",
                            "classified_by_email", "classified_at")

    def get_classified_by_email(self, movement: BankMovement) -> str | None:
        return movement.classified_by_user.email if movement.classified_by_user_id else None

    def validate(self, attrs):
        date = attrs.get("date")
        if date:
            attrs["period"] = f"{date.year}{date.month:02d}"
        return attrs


class MovementClassifySerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=MovementCategory.choices)


class BankStatementSerializer(serializers.ModelSerializer):
    from_ = serializers.DateField(source="period_from", read_only=True)
    to = serializers.DateField(source="period_to", read_only=True)

    class Meta:
        from .models import BankStatement
        model = BankStatement
        fields = ("id", "bank", "bank_account", "currency", "status", "error",
                  "movement_count", "original_name", "from_", "to", "created_at")
        read_only_fields = fields
