"""Tests for the read-only mailbox API."""

from __future__ import annotations

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from sunat_mailbox.models import Attachment, ExtractionStatus, MessageType

from .factories import TAXPAYER_ID, create_message


class MessageAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.notification = create_message(
            message_code=1, message_type=MessageType.NOTIFICATION,
            subject="Penalty resolution", attachment_count=1,
        )
        Attachment.objects.create(
            message=cls.notification, file_code=99, file_name="constancia.pdf"
        )
        cls.message = create_message(
            message_code=2, message_type=MessageType.MESSAGE,
            subject="Monthly filing reminder", is_read=True, attachment_count=0,
            published_at="2026-06-01T10:00:00-05:00", sent_on="2026-06-01",
        )
        cls.list_url = reverse("sunat_mailbox:message-list")

    def test_list_returns_all_messages(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)

    def test_list_is_ordered_by_published_at_desc(self):
        subjects = [r["subject"] for r in self.client.get(self.list_url).data["results"]]
        self.assertEqual(subjects, ["Penalty resolution", "Monthly filing reminder"])

    def test_filter_by_message_type(self):
        response = self.client.get(self.list_url, {"message_type": MessageType.MESSAGE})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["subject"], "Monthly filing reminder")

    def test_filter_by_read_state(self):
        response = self.client.get(self.list_url, {"is_read": "false"})
        self.assertEqual(response.data["count"], 1)

    def test_filter_by_has_attachments(self):
        self.assertEqual(
            self.client.get(self.list_url, {"has_attachments": "true"}).data["count"], 1
        )
        self.assertEqual(
            self.client.get(self.list_url, {"has_attachments": "false"}).data["count"], 1
        )

    def test_filter_by_date_range(self):
        response = self.client.get(self.list_url, {"sent_from": "2026-07-01"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["subject"], "Penalty resolution")

    def test_search_by_subject(self):
        response = self.client.get(self.list_url, {"search": "reminder"})
        self.assertEqual(response.data["count"], 1)

    def test_detail_includes_attachments(self):
        url = reverse("sunat_mailbox:message-detail", args=[self.notification.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["attachments"]), 1)
        self.assertEqual(response.data["attachments"][0]["file_name"], "constancia.pdf")

    def test_list_hides_raw_payloads(self):
        result = self.client.get(self.list_url).data["results"][0]
        self.assertNotIn("detail_payload", result)

    def test_summary_counts_by_type(self):
        url = reverse("sunat_mailbox:message-summary")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total"], 2)
        self.assertEqual(response.data["unread"], 1)
        self.assertEqual(response.data["with_attachments"], 1)
        # Regression: the view's default ordering used to leak into the GROUP BY and
        # turn every count into 1.
        self.assertEqual(response.data["by_type"], {"Notification": 1, "Message": 1})

    def test_summary_is_not_inflated_by_multiple_attachments(self):
        """The attachments join must not multiply a message by its attachment count."""
        Attachment.objects.create(
            message=self.notification, file_code=100, file_name="second.pdf"
        )
        response = self.client.get(reverse("sunat_mailbox:message-summary"))
        self.assertEqual(response.data["total"], 2)
        self.assertEqual(response.data["with_attachments"], 1)

    def test_summary_counts_messages_with_extracted_text(self):
        Attachment.objects.filter(message=self.notification).update(
            extraction_status=ExtractionStatus.EXTRACTED, text_content="hello"
        )
        response = self.client.get(reverse("sunat_mailbox:message-summary"))
        self.assertEqual(response.data["with_extracted_text"], 1)

    def test_search_matches_attachment_text(self):
        Attachment.objects.filter(message=self.notification).update(
            extraction_status=ExtractionStatus.EXTRACTED,
            text_content="Resolución de Ejecución Coactiva",
        )
        response = self.client.get(self.list_url, {"search": "Coactiva"})
        self.assertEqual(response.data["count"], 1)

    def test_filter_by_has_text(self):
        Attachment.objects.filter(message=self.notification).update(
            extraction_status=ExtractionStatus.EXTRACTED, text_content="hello"
        )
        self.assertEqual(
            self.client.get(self.list_url, {"has_text": "true"}).data["count"], 1
        )
        self.assertEqual(
            self.client.get(self.list_url, {"has_text": "false"}).data["count"], 1
        )

    def test_list_omits_attachment_text(self):
        result = self.client.get(self.list_url).data["results"][0]
        self.assertNotIn("attachments", result)

    def test_summary_respects_filters(self):
        url = reverse("sunat_mailbox:message-summary")
        response = self.client.get(url, {"message_type": MessageType.NOTIFICATION})
        self.assertEqual(response.data["total"], 1)

    def test_api_is_read_only(self):
        response = self.client.post(self.list_url, {"subject": "nope"})
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_filter_by_taxpayer_id(self):
        response = self.client.get(self.list_url, {"taxpayer_id": TAXPAYER_ID})
        self.assertEqual(response.data["count"], 2)
