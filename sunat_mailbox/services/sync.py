"""Maps SUNAT mailbox payloads onto the local models."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests
from django.db import transaction
from django.utils import timezone

from ..models import Attachment, ExtractionStatus, Message, MessageType
from .client import SunatMailboxClient
from .extraction import extract_text
from .parsing import (
    clean_text,
    parse_date,
    parse_datetime,
    parse_int,
    system_id_from_detail,
)

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Outcome of a synchronisation run."""

    created: int = 0
    updated: int = 0
    failed: int = 0
    attachments_downloaded: int = 0
    attachments_failed: int = 0

    @property
    def total(self) -> int:
        return self.created + self.updated

    def __str__(self) -> str:
        summary = f"{self.created} created, {self.updated} updated, {self.failed} failed"
        if self.attachments_downloaded or self.attachments_failed:
            summary += (
                f"; attachments: {self.attachments_downloaded} downloaded, "
                f"{self.attachments_failed} failed"
            )
        return summary


class MailboxSynchronizer:
    """Pulls messages from SUNAT and upserts them into the database.

    ``download_attachments`` implies ``fetch_details``: SUNAT only serves an
    attachment after its message detail was requested in the same session.
    """

    def __init__(
        self,
        client: SunatMailboxClient,
        *,
        fetch_details: bool = False,
        download_attachments: bool = False,
        redownload: bool = False,
    ):
        self.client = client
        self.fetch_details = fetch_details or download_attachments
        self.download_attachments = download_attachments
        self.redownload = redownload

    def run(
        self,
        message_types: list[int] | None = None,
        max_pages: int | None = None,
    ) -> SyncResult:
        result = SyncResult()
        for message_type in message_types or list(MessageType.values):
            for row in self.client.iter_messages(message_type, max_pages=max_pages):
                try:
                    self._save_row(row, message_type, result)
                except Exception:
                    # One malformed message must not abort the whole run.
                    result.failed += 1
                    logger.exception("Failed to save message %s", row.get("codMensaje"))
        return result

    def _save_row(
        self, row: dict[str, Any], message_type: int, result: SyncResult
    ) -> None:
        detail = None
        if self.fetch_details:
            detail = self.client.fetch_detail(row["codMensaje"], message_type)

        with transaction.atomic():
            message, created = Message.objects.update_or_create(
                taxpayer_id=self.client.taxpayer_id,
                message_code=row["codMensaje"],
                message_type=message_type,
                defaults=self._map_fields(row, detail),
            )
            attachments = (
                self._sync_attachments(message, detail) if detail is not None else []
            )
            result.created += created
            result.updated += not created

        # Downloads happen outside the transaction: they are slow network calls and a
        # failure on one file should not roll back the message itself.
        if self.download_attachments and attachments:
            self._download_all(attachments, system_id_from_detail(detail), result)

    def _map_fields(
        self, row: dict[str, Any], detail: dict[str, Any] | None
    ) -> dict[str, Any]:
        fields = {
            "subject": clean_text(row.get("desAsunto")),
            "sent_on": parse_date(row.get("fecEnvio")),
            "published_at": parse_datetime(row.get("fecPublica")),
            "expires_at": parse_datetime(row.get("fecVigencia")),
            "is_urgent": bool(row.get("indUrg")),
            "is_starred": bool(row.get("indDesta")),
            "attachment_count": row.get("cantidadArchAdj") or 0,
            "office_code": row.get("codDepen") or "",
            "label_code": row.get("codEtiqueta") or "",
            "status_code": parse_int(row.get("indEstado")),
            "list_payload": row,
        }
        if detail is not None:
            # fecLectura is the only unambiguous read marker SUNAT exposes; indEstado
            # has been observed changing without any message being opened, so it is
            # stored verbatim in status_code rather than interpreted.
            read_at = parse_datetime(detail.get("fecLectura"))
            fields["detail_payload"] = detail
            fields["sender_name"] = clean_text(detail.get("nombUsuario"))[:255]
            fields["read_at"] = read_at
            fields["is_read"] = read_at is not None
        return fields

    def _sync_attachments(
        self, message: Message, detail: dict[str, Any]
    ) -> list[Attachment]:
        """Reconcile the stored attachments against the detail payload.

        Rows are matched on ``(file_code, file_name)`` and updated in place rather
        than recreated, so previously extracted text survives a re-run. SUNAT also
        emits placeholder entries carrying no file at all; those are skipped.
        """
        rows = [
            item for item in (detail.get("listAttach") or [])
            if item.get("codArchivo") or item.get("nomArchivo")
        ]

        kept: list[Attachment] = []
        for item in rows:
            attachment, _ = Attachment.objects.update_or_create(
                message=message,
                file_code=parse_int(item.get("codArchivo")),
                file_name=clean_text(item.get("nomArchivo"))[:255],
                defaults={
                    "display_name": clean_text(item.get("nomAdjunto"))[:255],
                    "size_bytes": parse_int(item.get("cntTamarch")),
                    "size_display": clean_text(item.get("tamanoArchivoFormat"))[:50],
                    "external_id": parse_int(item.get("numId")),
                },
            )
            kept.append(attachment)

        message.attachments.exclude(pk__in=[a.pk for a in kept]).delete()
        return kept

    def _download_all(
        self, attachments: list[Attachment], system_id: str, result: SyncResult
    ) -> None:
        for attachment in attachments:
            if not self.redownload and attachment.downloaded_at:
                continue
            if not attachment.file_code:
                # Files served by another SUNAT subsystem (RVIE/RCE tickets, for one)
                # arrive with codArchivo unset or 0 and cannot be fetched from the
                # mailbox viewer. Record that instead of leaving them pending.
                self._mark(
                    attachment, ExtractionStatus.UNSUPPORTED,
                    "SUNAT exposes no codArchivo for this file; it is served by "
                    "another subsystem and needs a separate integration.",
                )
                continue
            try:
                self._download_one(attachment, system_id)
                result.attachments_downloaded += 1
            except requests.RequestException as exc:
                result.attachments_failed += 1
                self._mark(
                    attachment, ExtractionStatus.FAILED, f"{type(exc).__name__}: {exc}"
                )
                logger.warning(
                    "Download failed for attachment %s: %s", attachment.file_code, exc
                )

    def _download_one(self, attachment: Attachment, system_id: str) -> None:
        response = self.client.download_attachment(attachment.file_code, system_id)
        content_type = response.headers.get("Content-Type", "")
        extraction = extract_text(response.content, content_type=content_type)

        attachment.text_content = extraction.text
        attachment.page_count = extraction.page_count
        attachment.content_type = content_type[:100]
        attachment.checksum = extraction.checksum
        attachment.extraction_status = extraction.status
        attachment.extraction_error = extraction.error
        attachment.downloaded_at = timezone.now()
        if response.content:
            attachment.size_bytes = len(response.content)
        attachment.save()

    def _mark(self, attachment: Attachment, status: str, error: str) -> None:
        attachment.extraction_status = status
        attachment.extraction_error = error[:500]
        attachment.downloaded_at = timezone.now()
        attachment.save(
            update_fields=["extraction_status", "extraction_error", "downloaded_at"]
        )
