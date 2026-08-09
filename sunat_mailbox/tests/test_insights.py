"""Tests for the functional classification of mailbox messages."""

from __future__ import annotations

from django.urls import reverse
from rest_framework.test import APITestCase  # noqa: F401

from core.testing import TenantAPITestCase

from sunat_mailbox.models import Attachment, ExtractionStatus, MessageType
from sunat_mailbox.services import insights

from .factories import create_message

RVIE_MSJ = (
    '{"numruc":"20604442533","razonSocial":"PATTERN GROUP S.A.C.%22,'
    "%22listaDocumentos%22:%22Constancia de Recepci%26%23243;n de RVIE"
    "<br\\/>Archivo de Registro RVIE<br\\/>Constancia de Recepci%26%23243;n de RCE"
    "<br\\/>Propuesta de Casillas del Formulario Virtual 621%22,"
    '%22perPeriodoTributario%22:%22202606"}'
)


class ClassificationTests(TenantAPITestCase):
    def test_coactive_collection_is_urgent_and_requires_action(self):
        rule = insights.classify(
            "ASUNTO: Notificación de Resolución de Ejecución Coactiva N° 1",
            MessageType.NOTIFICATION,
        )
        self.assertEqual(rule["key"], "cobranza_coactiva")
        self.assertEqual(rule["priority"], insights.PRIORITY_URGENT)
        self.assertEqual(rule["action"], insights.ACTION_REQUIRED)

    def test_rvie_generation_is_informational_without_mandatory_action(self):
        rule = insights.classify(
            "Generación de Registro RVIE Y RCE del período 202606",
            MessageType.MESSAGE,
        )
        self.assertEqual(rule["key"], "registros_electronicos")
        self.assertEqual(rule["action"], insights.ACTION_NONE)
        self.assertIsNotNone(rule["recommendation"])

    def test_unmatched_notification_falls_back_to_notification_category(self):
        rule = insights.classify("Algo nuevo de SUNAT", MessageType.NOTIFICATION)
        self.assertEqual(rule["key"], "notificacion")

    def test_clean_subject_strips_asunto_prefix(self):
        self.assertEqual(
            insights.clean_subject("ASUNTO: Notificación Esquela N° 1"),
            "Notificación Esquela N° 1",
        )

    def test_tax_period_extraction(self):
        self.assertEqual(
            insights.extract_tax_period("Generación RVIE período 202606"), "2026-06"
        )
        self.assertEqual(
            insights.extract_tax_period("FV 621 período 06-2026"), "2026-06"
        )
        self.assertIsNone(insights.extract_tax_period("Resolución de Multa N° 023"))

    def test_expected_documents_parsed_and_grouped(self):
        groups = insights.expected_documents({"msjMensaje": RVIE_MSJ})
        self.assertEqual([g["group"] for g in groups], ["RVIE", "RCE"])
        self.assertIn("Constancia de Recepción de RVIE", groups[0]["items"])
        self.assertIn(
            "Propuesta de Casillas del Formulario Virtual 621", groups[1]["items"]
        )

    def test_expected_documents_empty_without_payload(self):
        self.assertEqual(insights.expected_documents(None), [])
        self.assertEqual(insights.expected_documents({"msjMensaje": "hola"}), [])


class DetailInsightsTests(TenantAPITestCase):
    def test_detail_reports_unavailable_files_with_human_note(self):
        message = create_message(
            subject="Generación de Registro RVIE Y RCE del período 202606",
            message_type=MessageType.MESSAGE,
            detail_payload={"msjMensaje": RVIE_MSJ, "codUsremisor": None},
            list_payload={"codUsremisor": "SUNAT"},
            attachment_count=1,
        )
        Attachment.objects.create(
            message=message,
            file_code=0,
            file_name="LE20604442533202606000804000111120.pdf",
            size_display="10,2 KB",
            extraction_status=ExtractionStatus.UNSUPPORTED,
            extraction_error="SUNAT exposes no codArchivo for this file",
        )
        url = reverse("sunat_mailbox:message-detail", args=[message.id])
        data = self.client.get(url).data["insights"]

        self.assertEqual(data["sender"], "SUNAT")
        self.assertEqual(data["tax_period_label"], "junio de 2026")
        self.assertEqual(data["files"][0]["status"], "unavailable")
        # The technical extraction error never surfaces in the executive block.
        self.assertNotIn("codArchivo", str(data))
        self.assertIn("no pudieron descargarse", data["summary"])


class CardEndpointTests(TenantAPITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.urgent = create_message(
            message_code=1,
            subject="ASUNTO: Notificación de Resolución de Multa N° 023",
            message_type=MessageType.NOTIFICATION,
            published_at="2026-07-22T16:17:43-05:00",
        )
        cls.info = create_message(
            message_code=2,
            subject="Generación de Registro RVIE Y RCE del período 202606",
            message_type=MessageType.MESSAGE,
            published_at="2026-07-16T17:19:15-05:00",
        )
        cls.read = create_message(
            message_code=3,
            subject="Reporte Resumen de comprobantes",
            message_type=MessageType.MESSAGE,
            is_read=True,
            published_at="2026-07-30T10:00:00-05:00",
        )
        cls.url = reverse("sunat_mailbox:message-card")

    def test_card_counts_and_status(self):
        data = self.client.get(self.url).data
        self.assertEqual(data["ui_status"], "critical")
        self.assertEqual(data["counts"]["unread"], 2)
        self.assertEqual(data["counts"]["urgent"], 1)
        self.assertEqual(data["counts"]["informational"], 1)

    def test_card_surfaces_most_relevant_unread_message(self):
        data = self.client.get(self.url).data
        # The urgent penalty wins over the more recent read report.
        self.assertEqual(data["latest_message"]["category"], "multa")
        self.assertEqual(data["latest_message"]["priority"], "urgent")

    def test_card_is_ok_when_everything_is_read(self):
        self.urgent.is_read = True
        self.urgent.save(update_fields=["is_read"])
        self.info.is_read = True
        self.info.save(update_fields=["is_read"])
        data = self.client.get(self.url).data
        self.assertEqual(data["ui_status"], "ok")
        self.assertIsNotNone(data["latest_message"])

    def test_reviewing_a_message_clears_it_from_the_counters(self):
        review_url = reverse("sunat_mailbox:message-review", args=[self.urgent.id])
        response = self.client.post(review_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_reviewed"])
        # SUNAT's own read state is never touched by an in-app review.
        self.assertFalse(response.data["is_read"])

        data = self.client.get(self.url).data
        self.assertEqual(data["counts"]["unread"], 1)
        self.assertEqual(data["counts"]["urgent"], 0)
        self.assertEqual(data["ui_status"], "requires_review")

    def test_review_is_idempotent(self):
        review_url = reverse("sunat_mailbox:message-review", args=[self.info.id])
        first = self.client.post(review_url).data["reviewed_at"]
        second = self.client.post(review_url).data["reviewed_at"]
        self.assertEqual(first, second)

    def test_reviewed_filter(self):
        self.client.post(
            reverse("sunat_mailbox:message-review", args=[self.urgent.id])
        )
        list_url = reverse("sunat_mailbox:message-list")
        pending = self.client.get(list_url, {"reviewed": "false"}).data
        self.assertEqual(pending["count"], 1)
        reviewed = self.client.get(list_url, {"reviewed": "true"}).data
        # The in-app reviewed message plus the one SUNAT reports as read.
        self.assertEqual(reviewed["count"], 2)
