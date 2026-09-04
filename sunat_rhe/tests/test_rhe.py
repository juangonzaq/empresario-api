"""Header-mapping, sync and API tests for received fee receipts. The
client reads whatever table SUNAT renders keyed by header text; these
fixtures cover the mapping, the upsert and the front's read endpoint."""

from __future__ import annotations

import datetime
import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse

from core import archive
from core.testing import TenantAPITestCase

from financials.models import FinancialTransaction
from financials.services.ingest import ingest_fee_receipts
from sunat_rhe.models import FeeReceipt
from sunat_rhe.services.parsing import rows_to_fields
from sunat_rhe.services.sync import RheSynchronizer

RUC = "20604442533"

# A row exactly as the client returns it: header text → cell text.
ROW = {
    "Fecha de Emisión": "05/08/2026",
    "RUC del Emisor": "10450123456",
    "Apellidos y Nombres": "PEREZ LOPEZ MARIA",
    "Serie": "E001",
    "Número": "245",
    "Moneda": "PEN",
    "Renta Bruta": "S/2,500.00",
    "Retención (8%)": "S/200.00",
    "Monto Neto": "S/2,300.00",
    "Estado": "Emitido",
}


class ParsingTests(TenantAPITestCase):
    def test_headers_map_by_alias(self):
        fields = rows_to_fields([ROW], RUC)[0]
        self.assertEqual(fields["issuer_doc"], "10450123456")
        self.assertEqual(fields["issuer_doc_type"], "6")
        self.assertEqual(fields["issuer_name"], "PEREZ LOPEZ MARIA")
        self.assertEqual(fields["full_number"], "E001-245")
        self.assertEqual(fields["period"], "202608")
        self.assertEqual(fields["gross_amount"], Decimal("2500.00"))
        self.assertEqual(fields["income_tax_withheld"], Decimal("200.00"))
        self.assertEqual(fields["net_amount"], Decimal("2300.00"))
        self.assertFalse(fields["is_reverted"])

    def test_combined_series_number_splits(self):
        row = {**ROW, "Serie": "", "Número": "E001-245"}
        del row["Serie"]
        fields = rows_to_fields([row], RUC)[0]
        self.assertEqual(fields["series"], "E001")
        self.assertEqual(fields["number"], "245")

    def test_reverted_status_is_flagged(self):
        fields = rows_to_fields([{**ROW, "Estado": "Revertido"}], RUC)[0]
        self.assertTrue(fields["is_reverted"])


PAGE = b"<html><body>RECIBO POR HONORARIOS E001-245</body></html>"


class FakeClient:
    """Rows as the real client returns them: the table row plus the
    detail (parsed) and the detail page (bytes) as annotations."""

    taxpayer_id = RUC

    def collect(self, periods):
        row = dict(ROW, __detail__={"concept": "Asesoría"}, __detail_html__=PAGE)
        return {periods[0]: [row]}


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="rhe-test-"))
class SyncTests(TenantAPITestCase):
    def test_upsert_is_idempotent(self):
        synchronizer = RheSynchronizer(FakeClient())
        first = synchronizer.sync_periods(["202608"])
        second = synchronizer.sync_periods(["202608"])
        self.assertEqual(first.created, 1)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.updated, 1)
        self.assertEqual(FeeReceipt.objects.count(), 1)

    def test_detail_page_is_filed_as_the_receipt_document(self):
        RheSynchronizer(FakeClient()).sync_periods(["202608"])
        row = FeeReceipt.objects.get()
        self.assertEqual(
            row.file.name,
            f"comprobantes/{RUC}/2026/08/recibo_honorarios/E001-245-{row.pk}.html",
        )
        with row.file.open("rb") as stored:
            self.assertEqual(stored.read(), PAGE)
        # The annotations never leak into the verbatim row.
        self.assertNotIn("__detail_html__", row.raw)
        self.assertNotIn("__detail__", row.raw)
        self.assertEqual(row.detail["concept"], "Asesoría")

    def test_resync_keeps_one_file_per_receipt(self):
        import os

        RheSynchronizer(FakeClient()).sync_periods(["202608"])
        before = FeeReceipt.objects.get().file.name
        RheSynchronizer(FakeClient()).sync_periods(["202608"])
        row = FeeReceipt.objects.get()
        self.assertEqual(row.file.name, before)
        # The temp MEDIA_ROOT outlives the DB rollback between tests, so
        # only this receipt's uuid is counted.
        folder = os.path.dirname(row.file.storage.path(before))
        copies = [name for name in os.listdir(folder) if str(row.pk) in name]
        self.assertEqual(copies, [os.path.basename(before)])

    def test_uploaded_pdf_is_never_replaced_by_the_page(self):
        row = receipt()  # same key as ROW: the scrape merges into it
        archive.store(row.file, b"%PDF-1.4 recibo", "pdf")
        row.save()
        RheSynchronizer(FakeClient()).sync_periods(["202608"])
        row.refresh_from_db()
        self.assertTrue(row.file.name.endswith(".pdf"))
        with row.file.open("rb") as stored:
            self.assertEqual(stored.read(), b"%PDF-1.4 recibo")


def receipt(**overrides) -> FeeReceipt:
    defaults = {
        "account_ruc": RUC,
        "issuer_doc": "10450123456",
        "issuer_name": "PEREZ LOPEZ MARIA",
        "series": "E001",
        "number": "245",
        "full_number": "E001-245",
        "issue_date": datetime.date(2026, 8, 5),
        "period": "202608",
        "gross_amount": Decimal("2500.00"),
        "income_tax_withheld": Decimal("200.00"),
        "net_amount": Decimal("2300.00"),
    }
    return FeeReceipt.objects.create(**{**defaults, **overrides})


class IngestTests(TenantAPITestCase):
    def test_receipt_becomes_a_confirmed_expense(self):
        row = receipt()
        result = ingest_fee_receipts(RUC)
        self.assertEqual(result["created"], 1)

        transaction = FinancialTransaction.objects.get(
            taxpayer_id=RUC, source="fee_receipt", external_id=str(row.pk)
        )
        self.assertEqual(transaction.direction, "outflow")
        # The expense is the GROSS fee: the 8 % withheld is still a cost.
        self.assertEqual(transaction.net_amount_pen, Decimal("2500.00"))
        self.assertEqual(transaction.category.code, "PROFESSIONAL_FEES")
        self.assertEqual(transaction.categorization_status, "confirmed")

    def test_reverted_receipt_never_enters(self):
        receipt(number="300", is_reverted=True)
        self.assertEqual(ingest_fee_receipts(RUC)["created"], 0)

    def test_recategorization_survives_resync(self):
        from financials.models import TransactionCategory

        row = receipt(number="301")
        ingest_fee_receipts(RUC)
        transaction = FinancialTransaction.objects.get(external_id=str(row.pk))
        transaction.category = TransactionCategory.objects.get(
            taxpayer_id="", code="SELLING_EXPENSES"
        )
        transaction.save(update_fields=["category"])

        ingest_fee_receipts(RUC)
        transaction.refresh_from_db()
        self.assertEqual(transaction.category.code, "SELLING_EXPENSES")


class ApiTests(TenantAPITestCase):
    def test_lists_the_period_with_totals(self):
        receipt()
        receipt(number="250", gross_amount=Decimal("1000.00"),
                income_tax_withheld=Decimal("0.00"),
                net_amount=Decimal("1000.00"))
        receipt(number="260", is_reverted=True, status="Revertido")

        response = self.client.get(reverse("sunat_rhe:receipts"))
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertEqual(data["period"], "202608")
        self.assertEqual(len(data["receipts"]), 3)
        # Reverted receipts are listed but never counted.
        self.assertEqual(Decimal(data["totals"]["gross"]), Decimal("3500.00"))
        self.assertEqual(Decimal(data["totals"]["withheld"]), Decimal("200.00"))
        self.assertEqual(Decimal(data["totals"]["net"]), Decimal("3300.00"))


class PdfDownloadTests(TenantAPITestCase):
    """Every receipt can be downloaded as PDF: scraped ones get a
    representation built from their data; uploaded ones return the file."""

    def test_scraped_receipt_gets_a_generated_pdf(self):
        row = receipt(detail={
            "concept": "Asesoría contable", "payment_method": "AL CONTADO",
            "observation": "-",
            "payments": [{"date": "05/08/2026", "gross": "2,500.00",
                          "withheld": "200.00", "net": "2,300.00"}],
        })
        response = self.client.get(reverse("sunat_rhe:receipt-pdf", args=[row.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn(
            'filename="RHE-10450123456-E001-245.pdf"', response["Content-Disposition"],
        )

    def test_receipt_of_another_company_is_not_served(self):
        row = receipt(account_ruc="20999999991")
        response = self.client.get(reverse("sunat_rhe:receipt-pdf", args=[row.pk]))
        self.assertEqual(response.status_code, 404)


class ManualRegistrationTests(TenantAPITestCase):
    """The product path: SUNAT gives the payer no listing, so the receipt
    is typed from the PDF and must land in the statement by itself."""

    def registrar(self, **overrides):
        payload = {
            "full_number": "E001-8",
            "issuer_doc": "10732009049",
            "issuer_name": "MATOS ABANTO IVANA ANDREA",
            "issue_date": "2026-08-17",
            "gross_amount": "1750.00",
            "income_tax_withheld": "0",
            **overrides,
        }
        return self.client.post(
            reverse("sunat_rhe:receipts"), payload, format="json"
        )

    def test_registered_receipt_reaches_the_statement(self):
        response = self.registrar()
        self.assertEqual(response.status_code, 201)
        row = FeeReceipt.objects.get(account_ruc=RUC, number="8")
        self.assertEqual(row.period, "202608")
        self.assertEqual(row.net_amount, Decimal("1750.00"))

        transaction = FinancialTransaction.objects.get(
            taxpayer_id=RUC, source="fee_receipt", external_id=str(row.pk)
        )
        self.assertEqual(transaction.category.code, "PROFESSIONAL_FEES")
        self.assertEqual(transaction.categorization_status, "confirmed")

    def test_duplicate_is_rejected(self):
        self.registrar()
        self.assertEqual(self.registrar().status_code, 409)

    def test_bad_document_is_rejected(self):
        response = self.registrar(issuer_doc="123")
        self.assertEqual(response.status_code, 400)

    def test_delete_removes_receipt_and_transaction(self):
        created = self.registrar().data
        row = FeeReceipt.objects.get(pk=created["id"])
        response = self.client.delete(
            reverse("sunat_rhe:receipt", args=[row.pk])
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(FeeReceipt.objects.filter(pk=row.pk).exists())
        self.assertFalse(FinancialTransaction.objects.filter(
            external_id=str(row.pk)
        ).exists())

    def test_scraped_receipt_cannot_be_deleted(self):
        row = receipt(number="500")
        response = self.client.delete(
            reverse("sunat_rhe:receipt", args=[row.pk])
        )
        self.assertEqual(response.status_code, 409)


def _sample_pdf() -> bytes:
    """A PDF with the exact wording SUNAT prints on an RHE."""
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    lines = [
        "MATOS ABANTO IVANA ANDREA",
        "R.U.C.   10732009049",
        "RECIBO POR HONORARIOS ELECTRONICO",
        "Nro:  E001- 8",
        "CAL. 45 MZ Q1 LT 05 URB EL PIN LIMA LIMA COMAS",
        "Recibí de:  PATTERN GROUP S.A.C.",
        "Identificado con RUC  número  20604442533",
        "La suma de: UN MIL SETECIENTOS CINCUENTA Y 00/100 SOLES",
        "Fecha de emisión  17  de  Agosto  del  2026",
        "Total por honorarios:  1,750.00",
        "Retención ( 8  %) IR:  (0.00)",
        "Total Neto Recibido:  1,750.00  SOLES",
    ]
    y = 800
    for line in lines:
        pdf.drawString(40, y, line)
        y -= 22
    pdf.save()
    return buffer.getvalue()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="rhe-test-"))
class PdfExtractTests(TenantAPITestCase):
    def test_reads_the_sunat_layout(self):
        from sunat_rhe.services.pdf_extract import extract_fee_receipt

        parsed = extract_fee_receipt(_sample_pdf(), RUC)
        self.assertEqual(parsed["full_number"], "E001-8")
        self.assertEqual(parsed["issuer_doc"], "10732009049")
        self.assertEqual(parsed["issuer_name"], "MATOS ABANTO IVANA ANDREA")
        self.assertEqual(parsed["issue_date"], "2026-08-17")
        self.assertEqual(parsed["gross_amount"], "1750.00")
        self.assertEqual(parsed["income_tax_withheld"], "0.00")

    def test_upload_creates_the_receipt(self):
        import io

        # reportlab stamps a random ID per document: keep the bytes sent.
        pdf_bytes = _sample_pdf()
        pdf = io.BytesIO(pdf_bytes)
        pdf.name = "recibo.pdf"
        response = self.client.post(
            reverse("sunat_rhe:receipt-upload"), {"file": pdf},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        row = FeeReceipt.objects.get(account_ruc=RUC, number="8")
        self.assertEqual(row.gross_amount, Decimal("1750.00"))
        self.assertTrue(FinancialTransaction.objects.filter(
            external_id=str(row.pk), source="fee_receipt"
        ).exists())
        # The PDF the worker handed over is filed with the scraped ones.
        self.assertEqual(
            row.file.name,
            f"comprobantes/{RUC}/2026/08/recibo_honorarios/E001-8-{row.pk}.pdf",
        )
        with row.file.open("rb") as stored:
            self.assertEqual(stored.read(), pdf_bytes)
        # The download hands back the very file the worker delivered.
        download = self.client.get(reverse("sunat_rhe:receipt-pdf", args=[row.pk]))
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b"".join(download.streaming_content), pdf_bytes)
        self.assertIn(
            'filename="RHE-10732009049-E001-8.pdf"', download["Content-Disposition"],
        )

    def test_scanned_pdf_falls_back_to_the_form(self):
        import io

        empty = io.BytesIO(b"%PDF-1.4 basura")
        empty.name = "scan.pdf"
        response = self.client.post(
            reverse("sunat_rhe:receipt-upload"), {"file": empty},
            format="multipart",
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("parsed", response.data)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="rhe-test-"))
class BackfillTests(TenantAPITestCase):
    """Initial load walks backwards until the history runs dry."""

    class Client:
        taxpayer_id = RUC

        def __init__(self):
            # Receipts exist only in two months; everything older is empty.
            self.data = {"202608": [ROW], "202607": [dict(ROW, **{"Número": "300"})]}
            self.asked: list[str] = []

        def collect(self, periods):
            self.asked.extend(periods)
            return {p: self.data.get(p, []) for p in periods}

    def test_stops_after_three_empty_months(self):
        client = self.Client()
        result = RheSynchronizer(client).backfill("202608", stop_after_empty=3)
        self.assertEqual(result.created, 2)
        # 202608, 202607 with data; then 06, 05, 04 empty → stop.
        self.assertEqual(
            client.asked,
            ["202608", "202607", "202606", "202605", "202604"],
        )

    def test_floor_is_respected(self):
        client = self.Client()
        client.data = {}
        result = RheSynchronizer(client).backfill(
            "201703", stop_after_empty=99, floor_period="201701"
        )
        self.assertEqual(result.created, 0)
        self.assertEqual(client.asked, ["201703", "201702", "201701"])
