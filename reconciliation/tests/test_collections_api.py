"""Cobranzas por cliente: agregado de InvoiceSettlement para el módulo Clientes."""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.test import override_settings
from django.urls import reverse

from core.testing import TenantAPITestCase
from reconciliation.models import InvoiceSettlement, ReconciliationRun
from reconciliation.tests.test_engine import RUC, PERIOD, cpe


def conciliado(period=PERIOD):
    return ReconciliationRun.objects.create(account_ruc=RUC, period=period, status="done")


def liquidar(invoice, *, status, paid="0", last_payment=None):
    total = invoice.total_amount or Decimal("0")
    pagado = Decimal(paid)
    return InvoiceSettlement.objects.create(
        account_ruc=RUC, invoice=invoice, status=status, invoice_total=total,
        paid_amount=pagado, balance=total - pagado, billing_period=invoice.period,
        last_payment_date=last_payment,
    )


@override_settings(SUNAT={"RUC": RUC})
class CollectionsApiTests(TenantAPITestCase):
    RUC = RUC

    def test_groups_by_customer_and_currency(self):
        conciliado()
        andes_1 = cpe("900", "1000.00")
        andes_1.issue_date = datetime.date(2026, 7, 5)
        andes_1.save(update_fields=["issue_date"])
        andes_2 = cpe("901", "500.00")
        andes_2.issue_date = datetime.date(2026, 7, 20)
        andes_2.save(update_fields=["issue_date"])
        costa = cpe("902", "2000.00", receiver="20222222222", receiver_name="COSTA EIRL")
        dolar = cpe("903", "300.00", receiver="20222222222", receiver_name="COSTA EIRL")
        dolar.currency = "USD"
        dolar.save(update_fields=["currency"])

        liquidar(andes_1, status="unpaid")
        liquidar(andes_2, status="partial", paid="200.00", last_payment=datetime.date(2026, 7, 25))
        liquidar(costa, status="paid", paid="2000.00", last_payment=datetime.date(2026, 7, 30))
        liquidar(dolar, status="unpaid")

        r = self.client.get(reverse("reconciliation:collections"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["periods"], ["202607"])
        resumen = r.data["summary"]
        self.assertEqual(resumen["invoices"], 4)
        self.assertEqual(resumen["pending_invoices"], 3)
        self.assertEqual(resumen["customers_with_debt"], 2)
        self.assertEqual(resumen["by_currency"]["PEN"]["pending"], Decimal("1300.00"))
        self.assertEqual(resumen["by_currency"]["USD"]["pending"], Decimal("300.00"))

        # Andes debe más en soles que Costa, así que encabeza la lista.
        andes, costa_fila = r.data["customers"][0], r.data["customers"][1]
        self.assertEqual(andes["ruc"], "20111111111")
        self.assertEqual(andes["pending_invoices"], 2)
        self.assertEqual(andes["by_currency"]["PEN"]["pending"], Decimal("1300.00"))
        self.assertEqual(andes["last_payment_date"], datetime.date(2026, 7, 25))
        # La más antigua con saldo es la del 5 de julio, completa.
        self.assertEqual(andes["oldest_unpaid"]["full_number"], "F001-900")
        self.assertEqual(andes["oldest_unpaid"]["balance"], Decimal("1000.00"))
        self.assertIsNotNone(andes["oldest_unpaid"]["days"])
        self.assertEqual(len(andes["pending_detail"]), 2)
        # Each pending invoice carries its id: the UI hangs the downloads on it.
        self.assertTrue(all(p["id"] for p in andes["pending_detail"]))

        self.assertEqual(costa_fila["ruc"], "20222222222")
        self.assertEqual(costa_fila["by_currency"]["PEN"]["paid"], Decimal("2000.00"))
        self.assertEqual(costa_fila["by_currency"]["PEN"]["pending"], Decimal("0"))
        self.assertEqual(costa_fila["by_currency"]["USD"]["pending"], Decimal("300.00"))

    def test_empty_without_reconciliation(self):
        # Con settlements pero sin corrida terminada no hay cobertura: el
        # payload no debe afirmar deudas de meses jamás conciliados.
        liquidar(cpe("905", "700.00"), status="unpaid")
        r = self.client.get(reverse("reconciliation:collections"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, {"periods": [], "summary": None, "customers": []})

    def test_credit_notes_reduce_pending(self):
        conciliado()
        factura = cpe("920", "1000.00")
        # El motor real resta la NC al reconstruir; aquí el settlement ya viene
        # neteado, y el endpoint debe publicar el desglose sin recontar la
        # factura cubierta como deuda.
        InvoiceSettlement.objects.create(
            account_ruc=RUC, invoice=factura, status="credited",
            invoice_total=Decimal("1000.00"), paid_amount=Decimal("0"),
            credit_notes_amount=Decimal("1000.00"), balance=Decimal("0"),
            billing_period=factura.period,
        )
        r = self.client.get(reverse("reconciliation:collections"))
        fila = r.data["customers"][0]
        self.assertEqual(fila["pending_invoices"], 0)
        self.assertEqual(fila["by_currency"]["PEN"]["credited"], Decimal("1000.00"))
        self.assertEqual(fila["by_currency"]["PEN"]["pending"], Decimal("0"))
        self.assertEqual(r.data["summary"]["customers_with_debt"], 0)

    def test_only_reconciled_periods_count(self):
        conciliado()
        julio = cpe("906", "400.00")
        junio = cpe("907", "900.00", period="202606")
        liquidar(julio, status="unpaid")
        liquidar(junio, status="unpaid")
        r = self.client.get(reverse("reconciliation:collections"))
        self.assertEqual(r.data["periods"], ["202607"])
        # La factura de junio queda fuera: ese mes no se ha conciliado.
        self.assertEqual(r.data["summary"]["invoices"], 1)
        self.assertEqual(r.data["summary"]["by_currency"]["PEN"]["pending"], Decimal("400.00"))

    def test_engine_nets_credit_notes(self):
        """El motor resta las NC: el pago neto casa y la anulada no es deuda."""
        from reconciliation.engine import matching
        from reconciliation.models import BankMovement, MovementKind
        from sunat_cpe.models import DocumentClass

        parcial = cpe("930", "1000.00")
        nc = cpe("70", "300.00", klass=DocumentClass.CREDIT_NOTE)
        # La referencia llega con espacios («F001 - 930»); el full_number, sin.
        nc.references_document = "F001 - 930"
        nc.save(update_fields=["references_document"])
        anulada = cpe("931", "500.00")
        nc2 = cpe("71", "500.00", klass=DocumentClass.CREDIT_NOTE)
        nc2.references_document = "F001-931"
        nc2.save(update_fields=["references_document"])
        BankMovement.objects.create(
            account_ruc=RUC, date=datetime.date(2026, 7, 20), period=PERIOD,
            kind=MovementKind.CREDIT, amount=Decimal("700.00"),
            description="TRANSFERENCIA DE 20111111111 CLIENTE ANDES",
        )

        matching.rebuild_settlements(RUC)

        st = InvoiceSettlement.objects.get(invoice=parcial)
        self.assertEqual(st.credit_notes_amount, Decimal("300.00"))
        self.assertEqual(st.paid_amount, Decimal("700.00"))
        self.assertEqual(st.balance, Decimal("0.00"))
        self.assertEqual(st.status, "paid")
        st2 = InvoiceSettlement.objects.get(invoice=anulada)
        self.assertEqual(st2.status, "credited")
        self.assertEqual(st2.balance, Decimal("0.00"))
        self.assertEqual(st2.paid_amount, Decimal("0"))

    def test_customer_documents_annotated(self):
        from sunat_cpe.models import DocumentClass

        conciliado()
        con_nc = cpe("940", "1000.00")
        nc = cpe("80", "300.00", klass=DocumentClass.CREDIT_NOTE)
        nc.references_document = "F001 - 940"
        nc.save(update_fields=["references_document"])
        junio = cpe("941", "500.00", period="202606")  # periodo sin conciliar
        cobrada = cpe("942", "700.00")
        suelta = cpe("81", "50.00", klass=DocumentClass.CREDIT_NOTE)
        suelta.references_document = "F001-999"  # no existe
        suelta.save(update_fields=["references_document"])
        liquidar(con_nc, status="partial", paid="200.00")
        liquidar(cobrada, status="paid", paid="700.00",
                 last_payment=datetime.date(2026, 7, 28))

        r = self.client.get(reverse("reconciliation:collections-customer"), {"ruc": "20111111111"})
        self.assertEqual(r.status_code, 200)
        docs = {d["full_number"]: d for d in r.data["documents"]}
        # Solo facturas como filas; la NC cuelga de la suya.
        self.assertEqual(set(docs), {"F001-940", "F001-941", "F001-942"})
        self.assertEqual(docs["F001-940"]["credit_notes"][0]["full_number"], "F001-80")
        self.assertTrue(docs["F001-940"]["credit_notes"][0]["id"])
        self.assertEqual(docs["F001-940"]["credit_notes_amount"], Decimal("300.00"))
        self.assertEqual(docs["F001-940"]["settlement"]["status"], "partial")
        self.assertEqual(docs["F001-942"]["settlement"]["status"], "paid")
        # Junio no se concilió: sin veredicto de cobranza.
        self.assertIsNone(docs["F001-941"]["settlement"])
        self.assertEqual(r.data["unassigned_credit_notes"][0]["full_number"], "F001-81")

        malo = self.client.get(reverse("reconciliation:collections-customer"), {"ruc": "abc"})
        self.assertEqual(malo.status_code, 400)

    def test_other_company_sees_nothing(self):
        factura = cpe("910", "800.00")
        liquidar(factura, status="unpaid")
        from accounts.tests.test_tenancy import make_org, make_user
        beto = make_user("beto@tres.pe")
        make_org("20200000003", beto)
        self.client.force_authenticate(beto)
        r = self.client.get(reverse("reconciliation:collections"))
        self.assertEqual(r.data["customers"], [])
