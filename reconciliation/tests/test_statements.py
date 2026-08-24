"""Bank statement upload: password derivation, decryption and parsing."""

from __future__ import annotations

import datetime
import io
import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from core.testing import TenantAPITestCase
from reconciliation.engine import statements as st
from reconciliation.models import BankMovement, BankStatement

RUC = "20604442533"

STATEMENT_LINES = [
    "BANCO DE CREDITO DEL PERU - ESTADO DE CUENTA - SOLES",
    "Cuenta 191-1234567-0-01",
    "FECHA  DESCRIPCION  MONTO  SALDO",
    "05/07/2026 ABONO TRANSFERENCIA RECIBIDA CLIENTE ANDES SAC  15,000.00  25,000.00",
    "08/07/2026 PAGO A PROVEEDOR SRL OP 445123  -3,200.50  21,799.50",
    "12/07/2026 COMISION MANTENIMIENTO  -12.00  21,787.50",
    "20/07/2026 DEPOSITO EN EFECTIVO  6,800.00  28,587.50",
    "Saldo final 28,587.50",
]


def make_pdf(lines: list[str], password: str | None) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in lines:
        c.drawString(40, y, line)
        y -= 18
    c.save()
    buf.seek(0)
    if not password:
        return buf.getvalue()
    reader = PdfReader(buf)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(password, algorithm="AES-256")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class DerivationTests(TenantAPITestCase):
    def test_password_is_ruc_digits_after_2nd_without_last(self):
        self.assertEqual(st.statement_password("20604442533"), "60444253")
        self.assertEqual(len(st.statement_password("20100000001")), 8)


class ParsingTests(TenantAPITestCase):
    def test_parses_dates_amounts_and_kind(self):
        text = "\n".join(STATEMENT_LINES)
        movements = st.parse_statement(text, default_year=2026)
        self.assertEqual(len(movements), 4)
        by_date = {m.date: m for m in movements}
        abono = by_date[datetime.date(2026, 7, 5)]
        self.assertEqual(abono.kind, "credit")
        self.assertEqual(abono.amount, Decimal("15000.00"))
        self.assertEqual(abono.balance, Decimal("25000.00"))
        pago = by_date[datetime.date(2026, 7, 8)]
        self.assertEqual(pago.kind, "debit")
        self.assertEqual(pago.amount, Decimal("3200.50"))
        self.assertEqual(pago.operation_number, "445123")

    def test_decimal_formats(self):
        self.assertEqual(st._to_decimal("15,000.00"), Decimal("15000.00"))
        self.assertEqual(st._to_decimal("1.234,56"), Decimal("1234.56"))
        self.assertEqual(st._to_decimal("-3,200.50"), Decimal("-3200.50"))
        self.assertEqual(st._to_decimal("(12.00)"), Decimal("-12.00"))


class DecryptTests(TenantAPITestCase):
    def test_extracts_text_from_encrypted_pdf_with_derived_password(self):
        pdf = make_pdf(STATEMENT_LINES, password="60444253")
        text = st.extract_text(pdf, [st.statement_password(RUC)])
        self.assertIn("ABONO TRANSFERENCIA", text)

    def test_wrong_password_raises(self):
        pdf = make_pdf(STATEMENT_LINES, password="99999999")
        with self.assertRaises(st.WrongPassword):
            st.extract_text(pdf, [st.statement_password(RUC)])


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="statements-test-"))
class UploadApiTests(TenantAPITestCase):
    RUC = RUC

    def _upload(self, pdf: bytes, **extra):
        return self.client.post(
            reverse("reconciliation:statements"),
            {"file": SimpleUploadedFile("bcp_soles.pdf", pdf, content_type="application/pdf"),
             "currency": "PEN", "bank": "BCP", **extra},
            format="multipart",
        )

    def test_upload_encrypted_statement_creates_movements(self):
        pdf = make_pdf(STATEMENT_LINES, password="60444253")
        r = self._upload(pdf)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["imported"], 4)
        self.assertEqual(BankMovement.objects.filter(account_ruc=RUC, source="statement").count(), 4)
        credit = BankMovement.objects.get(account_ruc=RUC, amount=Decimal("15000.00"))
        self.assertEqual(credit.kind, "credit")
        self.assertEqual(credit.currency, "PEN")
        self.assertEqual(credit.period, "202607")

    def test_dollar_statement_keeps_its_currency(self):
        pdf = make_pdf(["01/07/2026 ABONO WIRE TRANSFER  2,000.00  2,000.00"], password="60444253")
        r = self._upload(pdf, currency="USD", bank="BCP")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(BankMovement.objects.get(account_ruc=RUC, amount=Decimal("2000.00")).currency, "USD")

    def test_reupload_is_idempotent(self):
        pdf = make_pdf(STATEMENT_LINES, password="60444253")
        self._upload(pdf)
        self._upload(pdf)
        self.assertEqual(BankMovement.objects.filter(account_ruc=RUC).count(), 4)

    def test_wrong_password_returns_422_and_asks(self):
        pdf = make_pdf(STATEMENT_LINES, password="12345678")
        r = self._upload(pdf)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(BankStatement.objects.get().status, "locked")

    def test_manual_password_override(self):
        pdf = make_pdf(STATEMENT_LINES, password="OTRACLAVE")
        r = self._upload(pdf, password="OTRACLAVE")
        self.assertEqual(r.status_code, 201, r.data)

    def test_list_shows_hint_and_preview(self):
        r = self.client.get(reverse("reconciliation:statements"))
        self.assertEqual(r.data["default_password_preview"], "60444253")
        self.assertIn("después del 2", r.data["password_hint"])

    def test_viewer_cannot_upload(self):
        from accounts.models import Membership, Role
        from accounts.tests.test_tenancy import make_user
        viewer = make_user("lectura@uno.pe")
        Membership.objects.create(user=viewer, organization=self.organization, role=Role.VIEWER)
        self.client.force_authenticate(viewer)
        r = self._upload(make_pdf(STATEMENT_LINES, "60444253"))
        self.assertEqual(r.status_code, 403)

    def test_delete_removes_statement_and_its_movements(self):
        self._upload(make_pdf(STATEMENT_LINES, "60444253"))
        sid = BankStatement.objects.get().id
        r = self.client.delete(reverse("reconciliation:statement-delete", args=[sid]))
        self.assertEqual(r.status_code, 204)
        self.assertEqual(BankMovement.objects.filter(account_ruc=RUC).count(), 0)
