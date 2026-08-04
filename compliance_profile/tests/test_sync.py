"""Tests for the compliance synchroniser, driven by a stub client."""

from __future__ import annotations

from django.test import TestCase

from compliance_profile.models import ComplianceRating
from compliance_profile.services.sync import ComplianceSynchronizer

from .samples import CURRENT_HEADER, DETAIL, HISTORY, TAXPAYER_ID


class StubClient:
    taxpayer_id = TAXPAYER_ID

    def __init__(self):
        self.detail_calls: list[int] = []

    def fetch_current(self):
        return dict(CURRENT_HEADER)

    def fetch_history(self):
        return [dict(row) for row in HISTORY["cabecera"]]

    def fetch_detail(self, period):
        self.detail_calls.append(period)
        return DETAIL


class ComplianceSynchronizerTests(TestCase):
    def test_first_run_creates_all_quarters(self):
        client = StubClient()
        result = ComplianceSynchronizer(client).run()

        self.assertEqual(result.created, 3)
        self.assertEqual(result.updated, 0)
        self.assertEqual(result.details_fetched, 3)

        current = ComplianceRating.objects.current().get()
        self.assertEqual(current.period, 202602)
        self.assertEqual(current.rating, "D")
        self.assertEqual(current.variables.count(), 1)
        self.assertEqual(current.variables.first().record_count, 2)

    def test_second_run_is_idempotent_and_only_refetches_current(self):
        first_client = StubClient()
        ComplianceSynchronizer(first_client).run()

        client = StubClient()
        result = ComplianceSynchronizer(client).run()

        self.assertEqual(result.created, 0)
        self.assertEqual(result.updated, 3)
        # Historical quarters are immutable, so only the vigente one is refetched.
        self.assertEqual(client.detail_calls, [202602])
        self.assertEqual(ComplianceRating.objects.count(), 3)
        self.assertEqual(
            ComplianceRating.objects.current().get().variables.count(), 1
        )

    def test_current_flag_moves_with_sunat(self):
        ComplianceSynchronizer(StubClient()).run()

        class NewQuarterClient(StubClient):
            def fetch_current(self):
                return {**CURRENT_HEADER, "trimCal": 202603, "perEjec": 202608}

        ComplianceSynchronizer(NewQuarterClient()).run()

        current = ComplianceRating.objects.current().get()
        self.assertEqual(current.period, 202603)
        self.assertFalse(
            ComplianceRating.objects.get(period=202602).is_current
        )
