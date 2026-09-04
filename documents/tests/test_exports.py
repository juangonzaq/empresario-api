"""Descarga masiva: filtro → código al correo → .zip, una sola vez."""

from __future__ import annotations

import datetime
import io
import re
import tempfile
import zipfile
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from billing.services import ensure_subscription
from core import archive
from core.testing import TenantAPITestCase
from documents.models import MAX_ATTEMPTS, DocumentExport
from finance_analytics.tests.test_finance import UBL_INVOICE
from sunat_cpe.models import ElectronicInvoice
from sunat_rhe.models import FeeReceipt

RUC = "20604442533"
CODE = re.compile(r"\b(\d{6})\b")


def invoice(**overrides) -> ElectronicInvoice:
    defaults = {
        "account_ruc": RUC, "direction": "emitida", "document_class": "factura",
        "document_type": "01", "cpe_code": "01", "issuer_ruc": RUC,
        "issuer_name": "EMPRESA SAC", "receiver_ruc": "20100000001",
        "receiver_name": "CLIENTE SA", "series": "F001", "number": "10",
        "full_number": "F001-10", "issue_date": datetime.date(2026, 8, 5),
        "period": "202608", "currency": "PEN", "total_amount": Decimal("118.00"),
        "xml_content": UBL_INVOICE, "xml_sha256": "abc",
    }
    return ElectronicInvoice.objects.create(**{**defaults, **overrides})


def receipt(**overrides) -> FeeReceipt:
    defaults = {
        "account_ruc": RUC, "issuer_doc": "10450123456", "issuer_name": "PEREZ LOPEZ MARIA",
        "series": "E001", "number": "245", "full_number": "E001-245",
        "issue_date": datetime.date(2026, 8, 5), "period": "202608",
        "gross_amount": Decimal("2500.00"), "income_tax_withheld": Decimal("200.00"),
        "net_amount": Decimal("2300.00"),
    }
    return FeeReceipt.objects.create(**{**defaults, **overrides})


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="exports-test-"))
class ExportTests(TenantAPITestCase):
    def setUp(self):
        # Plan de pago vigente: la descarga masiva no entra en la prueba.
        sub = ensure_subscription(self.organization)
        sub.current_period_end = timezone.now() + datetime.timedelta(days=30)
        sub.save()
        mail.outbox = []

    def pedir(self, **payload):
        data = {"source": "cpe", "period_from": "202601", "period_to": "202612", **payload}
        return self.client.post(reverse("documents:export-request"), data, format="json")

    def bajar(self, export_id, code):
        return self.client.post(
            reverse("documents:export-download", args=[export_id]), {"code": code},
            format="json",
        )

    def codigo(self) -> str:
        return CODE.search(mail.outbox[-1].body).group(1)

    # ------------------------------------------------------------- pedir
    def test_trial_cannot_export(self):
        sub = ensure_subscription(self.organization)
        sub.current_period_end = None
        sub.save()
        invoice()
        response = self.pedir()
        self.assertEqual(response.status_code, 402)
        self.assertEqual(mail.outbox, [])

    def test_request_needs_documents(self):
        response = self.pedir()
        self.assertEqual(response.status_code, 400)
        self.assertIn("nada que descargar", response.data["detail"])
        self.assertEqual(mail.outbox, [])

    def test_bad_range_is_rejected(self):
        self.assertEqual(self.pedir(period_from="2026").status_code, 400)
        self.assertEqual(self.pedir(period_from="202612", period_to="202601").status_code, 400)
        self.assertEqual(self.pedir(source="otro").status_code, 400)
        self.assertEqual(self.pedir(document_classes=["boleta"]).status_code, 400)

    def test_request_counts_and_sends_the_code(self):
        invoice()
        invoice(number="11", full_number="F001-11")
        invoice(number="12", full_number="F001-12", document_class="nota_credito",
                document_type="07", cpe_code="07")
        invoice(number="13", full_number="F001-13", direction="recibida",
                issuer_ruc="20100000001")
        response = self.pedir(direction="emitida", document_classes=["factura"])
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data["document_count"], 2)
        self.assertEqual(response.data["email"], "pr***@empresa.pe")
        self.assertIn("facturas emitidas", response.data["label"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertRegex(mail.outbox[0].body, r"\d{6}")
        export = DocumentExport.objects.get(pk=response.data["id"])
        self.assertEqual(export.document_count, 2)
        self.assertEqual(export.user, self.user)
        self.assertIsNone(export.downloaded_at)

    def test_too_many_documents_ask_for_a_narrower_range(self):
        invoice()
        invoice(number="11", full_number="F001-11")
        with patch("documents.views.MAX_DOCUMENTS", 1):
            response = self.pedir()
        self.assertEqual(response.status_code, 400)
        self.assertIn("Acota", response.data["detail"])

    def test_new_request_closes_the_previous_code(self):
        invoice()
        first = self.pedir().data["id"]
        first_code = self.codigo()
        self.pedir()
        response = self.bajar(first, first_code)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "codigo_vencido")

    # ------------------------------------------------------------- bajar
    def test_download_with_the_code_returns_the_zip_once(self):
        invoice()
        invoice(number="12", full_number="F001-12", document_class="nota_credito",
                document_type="07", cpe_code="07", references_document="F001-10")
        invoice(number="14", full_number="F001-14", xml_content="", xml_sha256="")
        export_id = self.pedir().data["id"]
        response = self.bajar(export_id, self.codigo())
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn(f'filename="comprobantes-{RUC}-202601-202612.zip"',
                      response["Content-Disposition"])

        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            names = set(bundle.namelist())
            self.assertIn(f"2026/08/factura/{RUC}-01-F001-10.xml", names)
            self.assertIn(f"2026/08/factura/{RUC}-01-F001-10.pdf", names)
            self.assertIn(f"2026/08/nota_credito/{RUC}-07-F001-12.pdf", names)
            # Sin XML no hay archivos, pero sí fila en el índice.
            self.assertNotIn(f"2026/08/factura/{RUC}-01-F001-14.xml", names)
            self.assertTrue(bundle.read(f"2026/08/factura/{RUC}-01-F001-10.pdf").startswith(b"%PDF"))
            indice = bundle.read("indice.csv").decode("utf-8-sig")
            self.assertEqual(indice.count("\n"), 4)  # cabecera + 3 comprobantes
            self.assertIn("sin XML", indice)
            self.assertIn("LEEME.txt", names)

        export = DocumentExport.objects.get(pk=export_id)
        self.assertIsNotNone(export.downloaded_at)
        self.assertEqual(export.zip_bytes, len(response.content))
        # El código sirve una sola vez.
        again = self.bajar(export_id, self.codigo())
        self.assertEqual(again.status_code, 400)
        self.assertEqual(again.data["code"], "codigo_vencido")

    def test_wrong_code_burns_attempts_and_then_closes(self):
        invoice()
        export_id = self.pedir().data["id"]
        code = self.codigo()
        wrong = "000000" if code != "000000" else "111111"
        response = self.bajar(export_id, wrong)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "codigo_incorrecto")
        self.assertEqual(response.data["attempts_left"], MAX_ATTEMPTS - 1)
        for _ in range(MAX_ATTEMPTS - 1):
            self.bajar(export_id, wrong)
        # Agotados los intentos, ni el código bueno sirve.
        response = self.bajar(export_id, code)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "codigo_vencido")

    def test_expired_code_is_rejected(self):
        invoice()
        export_id = self.pedir().data["id"]
        DocumentExport.objects.filter(pk=export_id).update(
            expires_at=timezone.now() - datetime.timedelta(seconds=1)
        )
        response = self.bajar(export_id, self.codigo())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "codigo_vencido")

    def test_only_the_requester_can_download(self):
        invoice()
        export_id = self.pedir().data["id"]
        code = self.codigo()
        other, _ = self.make_tenant(RUC, "otra@empresa.pe")
        self.client.force_authenticate(other)
        self.assertEqual(self.bajar(export_id, code).status_code, 404)

    def test_fee_receipts_bundle_uploaded_and_generated_pdfs(self):
        subido = receipt()
        archive.store(subido.file, b"%PDF-1.4 entregado", "pdf")
        subido.save()
        receipt(number="300", full_number="E001-300", is_reverted=True, status="Revertido")
        response = self.pedir(source="rhe", period_from="202608", period_to="202608")
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data["document_count"], 2)
        zipped = self.bajar(response.data["id"], self.codigo())
        self.assertEqual(zipped.status_code, 200)
        self.assertIn(f'filename="honorarios-{RUC}-202608-202608.zip"',
                      zipped["Content-Disposition"])
        with zipfile.ZipFile(io.BytesIO(zipped.content)) as bundle:
            self.assertEqual(
                bundle.read("2026/08/recibo_honorarios/RHE-10450123456-E001-245.pdf"),
                b"%PDF-1.4 entregado",
            )
            generado = bundle.read("2026/08/recibo_honorarios/RHE-10450123456-E001-300.pdf")
            self.assertTrue(generado.startswith(b"%PDF"))
            self.assertIn("REVERTIDO", bundle.read("indice.csv").decode("utf-8-sig"))
