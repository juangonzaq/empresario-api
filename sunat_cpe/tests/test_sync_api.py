"""Sync (all types, upsert, XML) and API tests, driven by a stub client."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import override_settings
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


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="cpe-test-"))
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

    # ------------------------------------------------------ document archive
    def test_xml_is_filed_by_ruc_period_and_kind(self):
        CpeSynchronizer(StubClient()).sync_periods(["202607"])
        invoice = ElectronicInvoice.objects.get(series="E001", number="478")
        self.assertEqual(
            invoice.xml_file.name,
            f"comprobantes/{ACCOUNT}/2026/07/factura/E001-478-{invoice.pk}.xml",
        )
        with invoice.xml_file.open("rb") as stored:
            self.assertEqual(stored.read(), b"<Invoice>ok</Invoice>")
        credit_note = ElectronicInvoice.objects.filter(document_class="nota_credito").first()
        self.assertIn(f"/{ACCOUNT}/2026/07/nota_credito/", credit_note.xml_file.name)
        # Every downloadable comprobante got its copy on disk.
        self.assertFalse(ElectronicInvoice.objects.with_xml().filter(xml_file="").exists())

    def test_stored_xml_without_file_is_written_without_redownload(self):
        CpeSynchronizer(StubClient()).sync_periods(["202607"])
        invoice = ElectronicInvoice.objects.get(series="E001", number="478")
        invoice.xml_file.delete(save=True)

        client = StubClient()
        CpeSynchronizer(client).sync_periods(["202607"])
        self.assertEqual(client.downloaded, [])
        invoice.refresh_from_db()
        self.assertTrue(invoice.xml_file)
        self.assertTrue(invoice.xml_file.storage.exists(invoice.xml_file.name))

    def test_redownload_replaces_the_file_in_place(self):
        CpeSynchronizer(StubClient()).sync_periods(["202607"])
        before = ElectronicInvoice.objects.get(series="E001", number="478").xml_file.name

        CpeSynchronizer(StubClient(), redownload=True).sync_periods(["202607"])
        invoice = ElectronicInvoice.objects.get(series="E001", number="478")
        # Same deterministic path: no random suffix, no second copy.
        self.assertEqual(invoice.xml_file.name, before)

    def test_archive_xml_command_backfills_rows_without_file(self):
        CpeSynchronizer(StubClient()).sync_periods(["202607"])
        # Rows scraped before the archive existed: XML in the row, no file.
        ElectronicInvoice.objects.update(xml_file="")

        call_command("archive_xml", ruc=ACCOUNT)
        self.assertFalse(ElectronicInvoice.objects.with_xml().filter(xml_file="").exists())
        invoice = ElectronicInvoice.objects.get(series="E001", number="478")
        self.assertEqual(
            invoice.xml_file.name,
            f"comprobantes/{ACCOUNT}/2026/07/factura/E001-478-{invoice.pk}.xml",
        )


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="cpe-test-"))
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
        # SUNAT's naming: issuer RUC, type code, series and number.
        self.assertIn(
            f'filename="{invoice.issuer_ruc}-01-E001-478.xml"',
            response["Content-Disposition"],
        )

    def test_pdf_endpoint_renders_the_xml(self):
        from finance_analytics.tests.test_finance import UBL_INVOICE

        invoice = ElectronicInvoice.objects.get(series="E001", number="478")
        ElectronicInvoice.objects.filter(pk=invoice.pk).update(xml_content=UBL_INVOICE)
        response = self.client.get(
            reverse("sunat_cpe:cpe-invoice-pdf", args=[invoice.pk])
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn(
            f'filename="{invoice.issuer_ruc}-01-E001-478.pdf"',
            response["Content-Disposition"],
        )

    def test_pdf_needs_the_xml(self):
        invoice = ElectronicInvoice.objects.get(series="E001", number="478")
        ElectronicInvoice.objects.filter(pk=invoice.pk).update(xml_content="")
        response = self.client.get(
            reverse("sunat_cpe:cpe-invoice-pdf", args=[invoice.pk])
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_pdf_of_another_company_is_not_served(self):
        invoice = ElectronicInvoice.objects.get(series="E001", number="478")
        ElectronicInvoice.objects.filter(pk=invoice.pk).update(account_ruc="20999999991")
        response = self.client.get(
            reverse("sunat_cpe:cpe-invoice-pdf", args=[invoice.pk])
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_summary_splits_issued_received(self):
        response = self.client.get(reverse("sunat_cpe:cpe-invoice-summary"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], TOTAL)
        self.assertEqual(response.data["received"], 1)
        self.assertEqual(response.data["issued"], TOTAL - 1)
