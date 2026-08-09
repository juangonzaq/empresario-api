"""Sync and API tests, driven by a stub client over the real HTML fixture."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase  # noqa: F401

from core.testing import TenantAPITestCase

from sunat_itf.models import ItfRecord, ItfSection
from sunat_itf.services.sync import ItfSynchronizer

SAMPLE = (Path(__file__).parent / "sample_itf_result.html").read_text(encoding="utf-8")
TAXPAYER_ID = "20604442533"


class StubClient:
    taxpayer_id = TAXPAYER_ID

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def fetch_report(self, period_start, period_end):
        self.calls.append((period_start, period_end))
        return SAMPLE


class ItfSyncTests(TenantAPITestCase):
    def test_run_stores_records_and_is_idempotent(self):
        client = StubClient()
        result = ItfSynchronizer(client).run("202608")

        self.assertEqual(client.calls, [("202601", "202608")])
        self.assertGreater(result.stored, 0)
        stored = ItfRecord.objects.for_taxpayer(TAXPAYER_ID).count()
        self.assertEqual(stored, result.stored)

        # A second run replaces the range wholesale, not duplicates it.
        ItfSynchronizer(StubClient()).run("202608")
        self.assertEqual(ItfRecord.objects.for_taxpayer(TAXPAYER_ID).count(), stored)

    def test_records_land_in_accumulated_section(self):
        ItfSynchronizer(StubClient()).run("202608")
        self.assertTrue(
            ItfRecord.objects.in_section(ItfSection.ACCUMULATED).exists()
        )


class ItfApiTests(TenantAPITestCase):
    @classmethod
    def setUpTestData(cls):
        ItfSynchronizer(StubClient()).run("202608")

    def test_list_returns_records(self):
        response = self.client.get(reverse("sunat_itf:itf-record-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreater(response.data["count"], 0)

    def test_filter_by_period(self):
        response = self.client.get(
            reverse("sunat_itf:itf-record-list"), {"period": "202601"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for row in response.data["results"]:
            self.assertEqual(row["period"], "202601")

    def test_summary_totals_tax_per_period(self):
        response = self.client.get(reverse("sunat_itf:itf-record-summary"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("by_period", response.data)
        self.assertIsNotNone(response.data["tax_total"])
        self.assertGreater(Decimal(str(response.data["tax_total"])), 0)
