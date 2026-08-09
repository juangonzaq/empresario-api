"""Sync (all types, upsert, XML) and API tests, driven by a stub client."""

from __future__ import annotations

import json
from pathlib import Path

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase  # noqa: F401

from core.testing import TenantAPITestCase

from sunat_cpe.models import Direction, ElectronicInvoice
from sunat_cpe.services.sync import CpeSynchronizer

TESTS_DIR = Path(__file__).parent
ALL_TYPES = json.loads((TESTS_DIR / "sample_cpe_all_types.json").read_text())
ACCOUNT = "20604442533"
# total records across every tipo in the July fixture
TOTAL = sum(len(v) for v in ALL_TYPES.values())


class StubClient:
    """Serves the real per-tipo fixture for one period, empty before it."""

    taxpayer_id = ACCOUNT

    def __init__(self, data_period="202607"):
        self.data_period = data_period
        self.queried: list[tuple[str, str]] = []
        self.downloaded: list[str] = []

    def query(self, period, tipo, **kwargs):
        self.queried.append((period, tipo))
        return ALL_TYPES.get(tipo, []) if period == self.data_period else []

    def download_xml(self, fields):
        self.downloaded.append(f"{fields['series']}-{fields['number']}")
        return f"{fields['series']}-{fields['number']}.XML", "<Invoice>ok</Invoice>"


class CpeSyncTests(TenantAPITestCase):
    def test_sync_periods_stores_all_types_with_direction(self):
        client = StubClient()
        result = CpeSynchronizer(client).sync_periods(["202607"])

        self.assertEqual(result.created, TOTAL)
        self.assertEqual(ElectronicInvoice.objects.count(), TOTAL)
        # FE recibida keeps the third-party issuer.
        received = ElectronicInvoice.objects.received().get()
        self.assertEqual(received.direction, Direction.RECEIVED)
        self.assertNotEqual(received.issuer_ruc, ACCOUNT)
        # NC carries its reference back to the factura it modifies.
        nc = ElectronicInvoice.objects.filter(document_class="nota_credito")
        self.assertEqual(nc.count(), 2)

    def test_upsert_is_idempotent(self):
        CpeSynchronizer(StubClient()).sync_periods(["202607"])
        client2 = StubClient()
        result = CpeSynchronizer(client2).sync_periods(["202607"])
        self.assertEqual(result.created, 0)
        self.assertEqual(result.updated, TOTAL)
        self.assertEqual(ElectronicInvoice.objects.count(), TOTAL)
        # XML already present -> not re-downloaded.
        self.assertEqual(client2.downloaded, [])

    def test_backfill_walks_until_empty(self):
        client = StubClient(data_period="202607")
        result = CpeSynchronizer(client).backfill(
            "202608", stop_after_empty=2, floor_period="201401"
        )
        self.assertEqual(result.oldest_period, "202607")
        self.assertEqual(result.created, TOTAL)

    def test_xml_downloaded_for_downloadable(self):
        CpeSynchronizer(StubClient()).sync_periods(["202607"])
        self.assertTrue(ElectronicInvoice.objects.with_xml().exists())


class CpeApiTests(TenantAPITestCase):
    @classmethod
    def setUpTestData(cls):
        CpeSynchronizer(StubClient()).sync_periods(["202607"])

    def test_list_and_direction_filter(self):
        response = self.client.get(reverse("sunat_cpe:cpe-invoice-list"))
        self.assertEqual(response.data["count"], TOTAL)
        received = self.client.get(
            reverse("sunat_cpe:cpe-invoice-list"), {"direction": "recibida"}
        )
        self.assertEqual(received.data["count"], 1)

    def test_xml_endpoint(self):
        invoice = ElectronicInvoice.objects.get(series="E001", number="478")
        response = self.client.get(
            reverse("sunat_cpe:cpe-invoice-xml", args=[invoice.pk])
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(b"<Invoice>", response.content)

    def test_summary_splits_issued_received(self):
        response = self.client.get(reverse("sunat_cpe:cpe-invoice-summary"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], TOTAL)
        self.assertEqual(response.data["received"], 1)
        self.assertEqual(response.data["issued"], TOTAL - 1)
