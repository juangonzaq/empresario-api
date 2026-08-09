"""Tests for the intelligence pipeline with the LLM mocked out."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase  # noqa: F401

from core.testing import TenantAPITestCase

from sunat_mailbox.models import MessageType
from sunat_mailbox.tests.factories import TAXPAYER_ID, create_message
from sunat_intel.models import (
    AnalysisStatus, Case, CaseStatus, MessageAnalysis, Priority,
)
from sunat_intel.services import analyzer, cases


def llm_result(**overrides):
    base = {
        "comm_type": "Orden de pago",
        "priority": "high",
        "requires_action": True,
        "summary": "SUNAT emitió una orden de pago por IGV.",
        "why_it_matters": "Deuda exigible que puede pasar a cobranza coactiva.",
        "next_action": "Validar la deuda con el contador.",
        "tribute": "IGV",
        "tax_period": "2026-05",
        "references": ["030-001-4234055"],
        "amount": 4200.50,
        "amount_source": "adjunto:orden.pdf",
        "legal_deadline": None,
        "deadline_source": None,
        "missing_info": [],
        "confidence": "high",
        "sources": ["asunto", "adjunto:orden.pdf"],
    }
    return {**base, **overrides}


class AnalyzerTests(TenantAPITestCase):
    @patch("sunat_intel.services.analyzer.llm.structured_completion")
    def test_analysis_is_stored_and_cached(self, mock_llm):
        mock_llm.return_value = llm_result()
        message = create_message(message_code=10, subject="Orden de Pago N° 030-001-4234055")

        analyzer.analyze_pending()
        self.assertEqual(mock_llm.call_count, 1)
        analysis = MessageAnalysis.objects.get(message=message)
        self.assertEqual(analysis.status, AnalysisStatus.DONE)
        self.assertEqual(analysis.amount, Decimal("4200.50"))

        # Unchanged fingerprint → no second LLM call.
        analyzer.analyze_pending()
        self.assertEqual(mock_llm.call_count, 1)

    @patch("sunat_intel.services.analyzer.llm.structured_completion")
    def test_sourceless_amount_and_deadline_are_dropped(self, mock_llm):
        mock_llm.return_value = llm_result(
            amount=999, amount_source=None,
            legal_deadline="2026-09-01", deadline_source=None,
        )
        create_message(message_code=11)
        analyzer.analyze_pending()
        analysis = MessageAnalysis.objects.get()
        self.assertIsNone(analysis.amount)
        self.assertIsNone(analysis.legal_deadline)

    @patch("sunat_intel.services.analyzer.llm.structured_completion")
    def test_failed_analysis_is_recorded(self, mock_llm):
        mock_llm.side_effect = RuntimeError("boom")
        create_message(message_code=12)
        stats = analyzer.analyze_pending()
        self.assertEqual(stats, {"analyzed": 0, "failed": 1})
        self.assertEqual(MessageAnalysis.objects.get().status, AnalysisStatus.FAILED)


def make_analysis(message, **overrides):
    defaults = {
        "status": AnalysisStatus.DONE,
        "comm_type": "Orden de pago",
        "priority": Priority.HIGH,
        "requires_action": True,
        "summary": "resumen",
        "references": [],
    }
    return MessageAnalysis.objects.create(message=message, **{**defaults, **overrides})


class CaseGroupingTests(TenantAPITestCase):
    def test_related_messages_become_one_case(self):
        m1 = create_message(
            message_code=1, subject="Orden de Pago",
            published_at="2026-05-01T10:00:00-05:00",
        )
        m2 = create_message(
            message_code=2, subject="Ejecución Coactiva",
            published_at="2026-06-01T10:00:00-05:00",
        )
        m3 = create_message(
            message_code=3, subject="Resolución de Conclusión",
            published_at="2026-07-01T10:00:00-05:00",
        )
        make_analysis(m1, references=["030-001-4234055"], amount=Decimal("4200.50"),
                      amount_source="adjunto:op.pdf")
        make_analysis(m2, comm_type="Ejecución coactiva", priority=Priority.CRITICAL,
                      references=["N° 029-006-4233585", "030-001-4234055"])
        make_analysis(m3, comm_type="Resolución de conclusión",
                      priority=Priority.INFORMATIONAL, requires_action=False,
                      references=["029-006-4233585"],
                      summary="La cobranza concluyó.")

        stats = cases.rebuild_cases(TAXPAYER_ID)
        self.assertEqual(stats["created"], 1)
        case = Case.objects.get()
        self.assertEqual(case.messages.count(), 3)
        # The latest message is a conclusión → risk drops, no pending decision.
        self.assertEqual(case.risk, Priority.INFORMATIONAL)
        self.assertFalse(case.requires_decision)
        self.assertEqual(case.exposure_amount, Decimal("4200.50"))

    def test_informational_only_groups_do_not_create_cases(self):
        m = create_message(message_code=4, message_type=MessageType.MESSAGE)
        make_analysis(
            m, comm_type="Aviso", priority=Priority.INFORMATIONAL,
            requires_action=False,
        )
        stats = cases.rebuild_cases(TAXPAYER_ID)
        self.assertEqual(stats["created"], 0)
        self.assertEqual(Case.objects.count(), 0)

    def test_rebuild_preserves_human_fields(self):
        m = create_message(message_code=5)
        make_analysis(m, references=["023-002-2905092"])
        cases.rebuild_cases(TAXPAYER_ID)
        case = Case.objects.get()
        case.status = CaseStatus.DELEGATED
        case.responsible = "Contador"
        case.save()

        cases.rebuild_cases(TAXPAYER_ID)
        case.refresh_from_db()
        self.assertEqual(case.status, CaseStatus.DELEGATED)
        self.assertEqual(case.responsible, "Contador")


@override_settings(SUNAT_RUC=TAXPAYER_ID)
class ApiTests(TenantAPITestCase):
    def setUp(self):
        m1 = create_message(message_code=20, subject="Multa")
        make_analysis(
            m1, comm_type="Resolución de multa", priority=Priority.CRITICAL,
            references=["023-002-2905092"], amount=Decimal("1500.00"),
            amount_source="adjunto:multa.pdf",
            legal_deadline=date.today() + timedelta(days=10),
            deadline_source="adjunto:multa.pdf",
        )
        m2 = create_message(message_code=21, subject="Aviso", is_read=True)
        make_analysis(
            m2, comm_type="Aviso", priority=Priority.INFORMATIONAL,
            requires_action=False,
        )
        cases.rebuild_cases(TAXPAYER_ID)
        self.case = Case.objects.get()

    def test_overview_counts_and_separation_of_concerns(self):
        data = self.client.get(reverse("sunat_intel:overview")).data
        self.assertEqual(data["counts"]["open_cases"], 1)
        self.assertEqual(data["counts"]["critical_cases"], 1)
        self.assertEqual(data["counts"]["pending_decisions"], 1)
        self.assertEqual(data["counts"]["upcoming_deadlines"], 1)
        self.assertEqual(data["counts"]["informational_messages"], 1)
        # Unread is reported apart and does not inflate urgency.
        self.assertEqual(data["counts"]["unread_messages"], 1)
        self.assertEqual(data["exposure_total"], Decimal("1500.00"))
        self.assertIn("caso requiere atención", data["headline"])

    def test_case_detail_includes_messages_and_events(self):
        url = reverse("sunat_intel:case-detail", args=[self.case.id])
        data = self.client.get(url).data
        self.assertEqual(len(data["messages"]), 1)
        self.assertEqual(data["messages"][0]["comm_type"], "Resolución de multa")
        self.assertTrue(any(e["kind"] == "created" for e in data["events"]))

    def test_patch_updates_gestion_and_logs_actor(self):
        url = reverse("sunat_intel:case-detail", args=[self.case.id])
        response = self.client.patch(
            url,
            {"status": "delegado", "responsible": "Contador", "actor": "Juan Carlos"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "delegado")
        events = response.data["events"]
        self.assertTrue(
            any(e["actor"] == "Juan Carlos" and e["kind"] == "updated" for e in events)
        )

    def test_patch_rejects_invalid_status(self):
        url = reverse("sunat_intel:case-detail", args=[self.case.id])
        response = self.client.patch(url, {"status": "otro"}, format="json")
        self.assertEqual(response.status_code, 400)

    @patch("sunat_intel.services.ask.llm.structured_messages")
    def test_ask_returns_answer_with_sources_and_persists_history(self, mock_llm):
        mock_llm.return_value = {
            "answer": "Tienes una multa activa.",
            "sources": [{"kind": "case", "id": str(self.case.id), "label": "Multa"}],
            "has_sufficient_info": True,
        }
        response = self.client.post(
            reverse("sunat_intel:ask"), {"question": "¿Qué multas hay?"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["sources"][0]["kind"], "case")
        # The context sent to the model includes the company's messages.
        context = mock_llm.call_args.args[0][-1]["content"]
        self.assertIn("Resolución de multa", context)

        # Both turns were persisted and come back through the history endpoint.
        history = self.client.get(reverse("sunat_intel:vigia-history")).data
        self.assertEqual([m["role"] for m in history], ["user", "assistant"])
        self.assertEqual(history[1]["sources"][0]["kind"], "case")

        # A follow-up replays the stored turns as conversation context.
        self.client.post(
            reverse("sunat_intel:ask"), {"question": "¿Y cuánto es?"}, format="json"
        )
        replayed = mock_llm.call_args.args[0]
        self.assertEqual(replayed[1]["content"], "¿Qué multas hay?")
        self.assertEqual(replayed[2]["role"], "assistant")

    @patch("sunat_intel.services.ask.llm.structured_messages")
    def test_failed_ask_does_not_pollute_history(self, mock_llm):
        mock_llm.side_effect = RuntimeError("boom")
        response = self.client.post(
            reverse("sunat_intel:ask"), {"question": "hola"}, format="json"
        )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            self.client.get(reverse("sunat_intel:vigia-history")).data, []
        )

    def test_history_can_be_cleared(self):
        from sunat_intel.models import VigiaMessage

        VigiaMessage.objects.create(
            taxpayer_id=TAXPAYER_ID, role="user", content="hola"
        )
        response = self.client.delete(reverse("sunat_intel:vigia-history"))
        self.assertEqual(response.data["deleted"], 1)
        self.assertEqual(VigiaMessage.objects.count(), 0)

    def test_ask_requires_question(self):
        response = self.client.post(reverse("sunat_intel:ask"), {}, format="json")
        self.assertEqual(response.status_code, 400)
