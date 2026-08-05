"""Serializers for the read-only CPE API."""

from __future__ import annotations

from rest_framework import serializers

from .models import ElectronicInvoice


class ElectronicInvoiceListSerializer(serializers.ModelSerializer):
    direction_label = serializers.CharField(source="get_direction_display", read_only=True)
    document_class_label = serializers.CharField(
        source="get_document_class_display", read_only=True
    )
    has_xml = serializers.SerializerMethodField()

    class Meta:
        model = ElectronicInvoice
        fields = (
            "id", "account_ruc", "direction", "direction_label",
            "document_class", "document_class_label", "document_type",
            "series", "number", "full_number", "issue_date", "period",
            "issuer_ruc", "issuer_name", "receiver_ruc", "receiver_name",
            "currency", "total_amount", "status", "is_cancelled", "is_rejected",
            "references_document", "xml_id", "can_download", "has_xml",
        )

    def get_has_xml(self, obj: ElectronicInvoice) -> bool:
        return bool(obj.xml_content)


class ElectronicInvoiceDetailSerializer(ElectronicInvoiceListSerializer):
    class Meta(ElectronicInvoiceListSerializer.Meta):
        fields = ElectronicInvoiceListSerializer.Meta.fields + (
            "cpe_code", "download_code", "tipo_consulta", "receiver_doc_type",
            "currency_symbol", "reject_date", "last_seen_at",
            "xml_filename", "xml_sha256", "xml_downloaded_at", "xml_content",
            "raw", "created_at", "updated_at",
        )
