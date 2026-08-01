"""Persistence models for the SUNAT electronic mailbox (buzón SOL)."""

from __future__ import annotations

from django.db import models

from core.models import BaseModel


class MessageType(models.IntegerChoices):
    """SUNAT's ``tipoMsj`` discriminator."""

    MESSAGE = 1, "Message"
    NOTIFICATION = 2, "Notification"


class MessageQuerySet(models.QuerySet):
    def for_taxpayer(self, taxpayer_id: str) -> "MessageQuerySet":
        return self.filter(taxpayer_id=taxpayer_id)

    def unread(self) -> "MessageQuerySet":
        return self.filter(is_read=False)

    def with_attachments(self) -> "MessageQuerySet":
        return self.filter(attachment_count__gt=0)


class Message(BaseModel):
    """A single message or notification from the SUNAT electronic mailbox."""

    taxpayer_id = models.CharField(
        "RUC", max_length=11, db_index=True,
        help_text="Taxpayer identification number the message belongs to.",
    )
    message_code = models.BigIntegerField(help_text="SUNAT's codMensaje.")
    message_type = models.SmallIntegerField(choices=MessageType)

    subject = models.TextField(blank=True)
    sender_name = models.CharField(max_length=255, blank=True)
    office_code = models.CharField(max_length=10, blank=True)
    label_code = models.CharField(max_length=10, blank=True)

    sent_on = models.DateField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(
        null=True, blank=True,
        help_text="SUNAT's fecLectura. Only known once the detail has been fetched.",
    )

    is_read = models.BooleanField(
        default=False, help_text="Derived from read_at; see Attachment sync notes."
    )
    is_urgent = models.BooleanField(default=False)
    is_starred = models.BooleanField(default=False)
    attachment_count = models.PositiveIntegerField(default=0)
    status_code = models.SmallIntegerField(
        null=True, blank=True,
        help_text="SUNAT's indEstado, stored verbatim; its semantics are undocumented.",
    )

    # Raw SUNAT payloads, kept so new fields can be backfilled without re-scraping.
    list_payload = models.JSONField(default=dict, blank=True)
    detail_payload = models.JSONField(null=True, blank=True)

    objects = MessageQuerySet.as_manager()

    class Meta:
        ordering = ["-published_at", "-message_code"]
        indexes = [
            models.Index(fields=["taxpayer_id", "message_type"]),
            models.Index(fields=["-published_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["taxpayer_id", "message_code", "message_type"],
                name="unique_message_per_taxpayer",
            )
        ]

    def __str__(self) -> str:
        return f"[{self.taxpayer_id}] {self.sent_on} {self.subject[:60]}"


class ExtractionStatus(models.TextChoices):
    """State of the text extraction for an attachment."""

    PENDING = "pending", "Not downloaded yet"
    EXTRACTED = "extracted", "Text extracted"
    EMPTY = "empty", "PDF has no text layer (needs OCR)"
    UNSUPPORTED = "unsupported", "Not a PDF"
    FAILED = "failed", "Download or extraction failed"


class AttachmentQuerySet(models.QuerySet):
    def with_text(self) -> "AttachmentQuerySet":
        return self.filter(extraction_status=ExtractionStatus.EXTRACTED)

    def needs_ocr(self) -> "AttachmentQuerySet":
        return self.filter(extraction_status=ExtractionStatus.EMPTY)

    def pending(self) -> "AttachmentQuerySet":
        return self.filter(extraction_status=ExtractionStatus.PENDING)


class Attachment(BaseModel):
    """A file attached to a message (``listAttach`` in SUNAT's detail payload)."""

    message = models.ForeignKey(
        Message, related_name="attachments", on_delete=models.CASCADE
    )
    file_code = models.BigIntegerField(null=True, blank=True, help_text="SUNAT's codArchivo.")
    file_name = models.CharField(max_length=255, blank=True)
    display_name = models.CharField(max_length=255, blank=True)
    size_bytes = models.BigIntegerField(null=True, blank=True)
    size_display = models.CharField(max_length=50, blank=True)
    external_id = models.BigIntegerField(null=True, blank=True, help_text="SUNAT's numId.")

    # Downloaded content. The bytes themselves are not stored; only the extracted
    # text, which is what downstream analysis consumes.
    text_content = models.TextField(blank=True)
    page_count = models.PositiveIntegerField(null=True, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    checksum = models.CharField(
        max_length=64, blank=True, db_index=True,
        help_text="SHA-256 of the downloaded bytes; identical files share a checksum.",
    )
    extraction_status = models.CharField(
        max_length=20, choices=ExtractionStatus, default=ExtractionStatus.PENDING,
        db_index=True,
    )
    extraction_error = models.TextField(blank=True)
    downloaded_at = models.DateTimeField(null=True, blank=True)

    objects = AttachmentQuerySet.as_manager()

    class Meta:
        ordering = ["created_at"]
        # No unique constraint on file_code: SUNAT reuses 0 across several attachments
        # of the same message. Sync replaces a message's attachments wholesale, so
        # duplicates cannot accumulate across runs.
        indexes = [models.Index(fields=["message", "file_code"])]

    def __str__(self) -> str:
        return self.file_name or self.display_name or f"attachment {self.pk}"
