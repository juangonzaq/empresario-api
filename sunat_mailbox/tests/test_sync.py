"""Tests for the SUNAT payload -> model mapping."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from django.test import TestCase

from sunat_mailbox.models import Attachment, Message, MessageType
from sunat_mailbox.services.sync import MailboxSynchronizer

from .factories import TAXPAYER_ID, detail_payload, list_row


def build_client(rows, detail=None):
    client = MagicMock()
    client.taxpayer_id = TAXPAYER_ID
    client.iter_messages.side_effect = lambda message_type, max_pages=None: iter(
        rows if message_type == MessageType.NOTIFICATION else []
    )
    client.fetch_detail.return_value = detail
    return client


class SyncTests(TestCase):
    def test_maps_list_row_onto_message(self):
        synchronizer = MailboxSynchronizer(build_client([list_row()]))
        result = synchronizer.run(message_types=[MessageType.NOTIFICATION])

        self.assertEqual(result.created, 1)
        self.assertEqual(result.failed, 0)

        message = Message.objects.get()
        self.assertEqual(message.taxpayer_id, TAXPAYER_ID)
        self.assertEqual(message.message_code, 1043884152)
        self.assertEqual(message.office_code, "0023")
        self.assertEqual(message.status_code, 1)
        self.assertEqual(str(message.sent_on), "2026-07-22")

    def test_unescapes_html_entities_in_subject(self):
        MailboxSynchronizer(build_client([list_row()])).run(
            message_types=[MessageType.NOTIFICATION]
        )
        self.assertEqual(
            Message.objects.get().subject, "Notificación de Resolución de Multa"
        )

    def test_primary_key_is_a_uuid(self):
        MailboxSynchronizer(build_client([list_row()])).run(
            message_types=[MessageType.NOTIFICATION]
        )
        self.assertIsInstance(Message.objects.get().pk, uuid.UUID)

    def test_read_state_comes_from_fec_lectura_not_ind_estado(self):
        """indEstado has been seen changing on its own, so only fecLectura is trusted."""
        client = build_client([list_row(indEstado=1)], detail=detail_payload())
        MailboxSynchronizer(client, fetch_details=True).run(
            message_types=[MessageType.NOTIFICATION]
        )
        message = Message.objects.get()
        self.assertFalse(message.is_read)
        self.assertIsNone(message.read_at)

        client = build_client(
            [list_row(indEstado=1)],
            detail=detail_payload(fecLectura="23/07/2026 09:00:00"),
        )
        MailboxSynchronizer(client, fetch_details=True).run(
            message_types=[MessageType.NOTIFICATION]
        )
        message.refresh_from_db()
        self.assertTrue(message.is_read)
        self.assertIsNotNone(message.read_at)

    def test_skips_placeholder_attachments(self):
        client = build_client([list_row()], detail=detail_payload())
        MailboxSynchronizer(client, fetch_details=True).run(
            message_types=[MessageType.NOTIFICATION]
        )
        self.assertEqual(Attachment.objects.count(), 1)
        self.assertEqual(
            Attachment.objects.get().file_name, "constancia_20260722154537.pdf"
        )

    def test_rerun_updates_instead_of_duplicating(self):
        for _ in range(2):
            MailboxSynchronizer(build_client([list_row()])).run(
                message_types=[MessageType.NOTIFICATION]
            )
        self.assertEqual(Message.objects.count(), 1)

    def test_attachments_are_replaced_not_accumulated(self):
        client = build_client([list_row()], detail=detail_payload())
        for _ in range(2):
            MailboxSynchronizer(client, fetch_details=True).run(
                message_types=[MessageType.NOTIFICATION]
            )
        self.assertEqual(Attachment.objects.count(), 1)

    def test_duplicate_file_codes_are_allowed(self):
        """SUNAT reuses codArchivo=0 across attachments of the same message."""
        detail = detail_payload(listAttach=[
            {"codArchivo": 0, "nomArchivo": "a.pdf", "numId": 1},
            {"codArchivo": 0, "nomArchivo": "b.pdf", "numId": 2},
        ])
        client = build_client([list_row()], detail=detail)
        result = MailboxSynchronizer(client, fetch_details=True).run(
            message_types=[MessageType.NOTIFICATION]
        )
        self.assertEqual(result.failed, 0)
        self.assertEqual(Attachment.objects.count(), 2)

    def test_a_broken_row_does_not_abort_the_run(self):
        rows = [list_row(codMensaje=1), list_row(codMensaje=None), list_row(codMensaje=3)]
        with self.assertLogs("sunat_mailbox.services.sync", level="ERROR") as logs:
            result = MailboxSynchronizer(build_client(rows)).run(
                message_types=[MessageType.NOTIFICATION]
            )
        self.assertEqual(result.created, 2)
        self.assertEqual(result.failed, 1)
        self.assertEqual(len(logs.records), 1)
