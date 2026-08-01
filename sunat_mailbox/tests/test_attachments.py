"""Tests for attachment download and PDF text extraction."""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import requests
from django.test import TestCase
from pypdf import PdfWriter

from sunat_mailbox.models import Attachment, ExtractionStatus, MessageType
from sunat_mailbox.services.extraction import extract_text, sha256
from sunat_mailbox.services.parsing import system_id_from_detail
from sunat_mailbox.services.sync import MailboxSynchronizer

from .factories import TAXPAYER_ID, detail_payload, list_row


def blank_pdf(pages: int = 1) -> bytes:
    """A structurally valid PDF with no text layer, like a scanned document."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def fake_response(content: bytes, content_type: str = "application/pdf"):
    response = MagicMock(spec=requests.Response)
    response.content = content
    response.headers = {"Content-Type": content_type}
    return response


def build_client(rows, detail, download=None):
    client = MagicMock()
    client.taxpayer_id = TAXPAYER_ID
    client.iter_messages.side_effect = lambda message_type, max_pages=None: iter(
        rows if message_type == MessageType.NOTIFICATION else []
    )
    client.fetch_detail.return_value = detail
    client.download_attachment.return_value = download or fake_response(blank_pdf())
    return client


class ExtractTextTests(TestCase):
    def test_reports_pdf_without_text_layer_as_empty(self):
        result = extract_text(blank_pdf(pages=2))
        self.assertEqual(result.status, ExtractionStatus.EMPTY)
        self.assertEqual(result.page_count, 2)
        self.assertEqual(result.text, "")

    def test_rejects_non_pdf_content(self):
        result = extract_text(b"<html>error page</html>", content_type="text/html")
        self.assertEqual(result.status, ExtractionStatus.UNSUPPORTED)
        self.assertIn("text/html", result.error)

    def test_reports_empty_body_as_failed(self):
        self.assertEqual(extract_text(b"").status, ExtractionStatus.FAILED)

    def test_reports_corrupt_pdf_as_failed(self):
        result = extract_text(b"%PDF-1.7 truncated garbage")
        self.assertEqual(result.status, ExtractionStatus.FAILED)
        self.assertTrue(result.error)

    def test_checksum_matches_the_bytes(self):
        data = blank_pdf()
        self.assertEqual(extract_text(data).checksum, sha256(data))


class SystemIdTests(TestCase):
    def test_reads_system_id_from_json_body(self):
        detail = {"msjMensaje": '{"sistema":"7","id_archivo":"1"}'}
        self.assertEqual(system_id_from_detail(detail), "7")

    def test_falls_back_when_body_is_html(self):
        self.assertEqual(system_id_from_detail({"msjMensaje": "<p>hola</p>"}), "0")

    def test_falls_back_when_detail_is_missing(self):
        self.assertEqual(system_id_from_detail(None), "0")


class AttachmentSyncTests(TestCase):
    def sync(self, client, **kwargs):
        return MailboxSynchronizer(client, download_attachments=True, **kwargs).run(
            message_types=[MessageType.NOTIFICATION]
        )

    def test_downloads_and_stores_extracted_state(self):
        client = build_client([list_row()], detail_payload())
        result = self.sync(client)

        self.assertEqual(result.attachments_downloaded, 1)
        self.assertEqual(result.attachments_failed, 0)

        attachment = Attachment.objects.get()
        self.assertEqual(attachment.extraction_status, ExtractionStatus.EMPTY)
        self.assertEqual(attachment.content_type, "application/pdf")
        self.assertIsNotNone(attachment.downloaded_at)
        self.assertTrue(attachment.checksum)

    def test_downloading_implies_fetching_details(self):
        client = build_client([list_row()], detail_payload())
        self.sync(client)
        client.fetch_detail.assert_called_once()

    def test_already_downloaded_attachments_are_skipped(self):
        client = build_client([list_row()], detail_payload())
        self.sync(client)
        self.sync(client)
        self.assertEqual(client.download_attachment.call_count, 1)

    def test_redownload_forces_a_refetch(self):
        client = build_client([list_row()], detail_payload())
        self.sync(client)
        self.sync(client, redownload=True)
        self.assertEqual(client.download_attachment.call_count, 2)

    def test_extracted_text_survives_a_resync(self):
        """Attachments are reconciled in place, not deleted and recreated."""
        client = build_client([list_row()], detail_payload())
        self.sync(client)
        Attachment.objects.update(
            text_content="already extracted", extraction_status=ExtractionStatus.EXTRACTED
        )

        self.sync(client)
        attachment = Attachment.objects.get()
        self.assertEqual(attachment.text_content, "already extracted")
        self.assertEqual(attachment.extraction_status, ExtractionStatus.EXTRACTED)

    def test_a_failed_download_is_recorded_and_does_not_abort(self):
        client = build_client([list_row()], detail_payload())
        client.download_attachment.side_effect = requests.ConnectionError("boom")

        result = self.sync(client)
        self.assertEqual(result.failed, 0)
        self.assertEqual(result.attachments_failed, 1)

        attachment = Attachment.objects.get()
        self.assertEqual(attachment.extraction_status, ExtractionStatus.FAILED)
        self.assertIn("boom", attachment.extraction_error)

    def test_files_without_a_code_are_marked_unsupported(self):
        """RVIE/RCE files arrive with codArchivo=0 and cannot be downloaded here."""
        detail = detail_payload(listAttach=[
            {"codArchivo": 0, "nomArchivo": "LE20604442533.pdf", "cntTamarch": 10399},
        ])
        client = build_client([list_row()], detail)
        self.sync(client)

        client.download_attachment.assert_not_called()
        attachment = Attachment.objects.get()
        self.assertEqual(attachment.extraction_status, ExtractionStatus.UNSUPPORTED)
        self.assertIn("codArchivo", attachment.extraction_error)

    def test_empty_body_from_sunat_is_recorded_as_failed(self):
        client = build_client([list_row()], detail_payload(), download=fake_response(b""))
        self.sync(client)
        self.assertEqual(
            Attachment.objects.get().extraction_status, ExtractionStatus.FAILED
        )
