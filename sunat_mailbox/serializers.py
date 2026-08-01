"""Serializers for the read-only mailbox API."""

from __future__ import annotations

from rest_framework import serializers

from .models import Attachment, Message


class AttachmentSerializer(serializers.ModelSerializer):
    """Attachment metadata without the extracted text (which can be large)."""

    class Meta:
        model = Attachment
        fields = (
            "id", "file_code", "file_name", "display_name",
            "size_bytes", "size_display", "external_id", "content_type",
            "page_count", "checksum", "extraction_status", "downloaded_at",
        )


class AttachmentContentSerializer(AttachmentSerializer):
    """Adds the extracted text, for the detail view and the content endpoint."""

    class Meta(AttachmentSerializer.Meta):
        fields = AttachmentSerializer.Meta.fields + ("text_content", "extraction_error")


class MessageListSerializer(serializers.ModelSerializer):
    """Compact representation used for list endpoints."""

    message_type_label = serializers.CharField(
        source="get_message_type_display", read_only=True
    )

    class Meta:
        model = Message
        fields = (
            "id", "taxpayer_id", "message_code", "message_type", "message_type_label",
            "subject", "sender_name", "sent_on", "published_at", "expires_at",
            "read_at", "is_read", "is_urgent", "is_starred", "attachment_count",
            "office_code", "label_code", "status_code",
        )


class MessageDetailSerializer(MessageListSerializer):
    """Full representation, including attachments and the raw SUNAT payloads."""

    attachments = AttachmentContentSerializer(many=True, read_only=True)

    class Meta(MessageListSerializer.Meta):
        fields = MessageListSerializer.Meta.fields + (
            "attachments", "list_payload", "detail_payload",
            "created_at", "updated_at",
        )
