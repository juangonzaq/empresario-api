"""Deterministic reconciliation engine, end to end on synthetic data."""

from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from core.testing import TenantAPITestCase
from finance_analytics.models import AlertStatus, FinanceAlert
from reconciliation.engine import banking, cpe_sire, matching, run as runner
from reconciliation.models import (
    BankMovement, ConsistencyScore, DeclaredSummary, DocMatchStatus,
    DocumentReconciliation, InvoiceSettlement, MatchLevel, MovementCategory,
    MovementKind, SettlementStatus,
)
from sensor_sunat.models import PurchaseDoc, SalesDoc
from sunat_cpe.models import Direction, DocumentClass, ElectronicInvoice

RUC = "20604442533"
PERIOD = "202607"


def cpe(number, total, *, direction=Direction.ISSUED, klass=DocumentClass.INVOICE,
        doc_type="01", series="F001", period=PERIOD, cancelled=False, receiver="20111111111",
        receiver_name="CLIENTE ANDES SAC", n=[0]):
    n[0] += 1
    return ElectronicInvoice.objects.create(
        account_ruc=RUC, direction=direction, document_class=klass, document_type=doc_type,
        issuer_ruc=RUC if direction == Direction.ISSUED else "20999999999",
        issuer_name="PROVEEDOR SRL" if direction == Direction.RECEIVED else "PATTERN",
        series=series, number=str(number), full_number=f"{series}-{number}", period=period,
        currency="PEN", total_amount=Decimal(total), is_cancelled=cancelled,
        receiver_ruc=receiver, receiver_name=receiver_name,
    )


def sire_sale(number, total, igv=None, *, series="F001", doc_type="01", period=PERIOD):
    return SalesDoc.objects.create(
        tax_period=period, doc_type=doc_type, series=series, number=str(number),
        issue_date=datetime.date(2026, 7, 10), customer_ruc="20111111111",
        customer_name="CLIENTE ANDES SAC", base_amount=None, igv=igv, total=Decimal(total),
    )


@override_settings(SUNAT={"RUC": RUC})
class CpeSireTests(TenantAPITestCase):
    RUC = RUC

    def test_matches_misses_and_amount_differences(self):
        cpe("100", "1180.00"); sire_sale("100", "1180.00")            # match
        cpe("101", "2360.00")                                         # CPE only (grande → crítico)
        cpe("102", "590.00")                                          # CPE only (chico → revisar)
        sire_sale("103", "800.00")                                    # SIRE only
        cpe("104", "1000.00"); sire_sale("104", "900.00")             # monto distinto
        cpe("105", "500.00", cancelled=True)                          # anulado y ausente: OK
        result = cpe_sire.reconcile_direction(RUC, PERIOD, "sales")
        by_key = {r["doc_key"]: r for r in result["rows"]}
        self.assertEqual(by_key["01-F001-100"]["status"], DocMatchStatus.MATCHED)
        self.assertEqual(by_key["01-F001-101"]["status"], DocMatchStatus.CPE_ONLY)
        self.assertEqual(by_key["01-F001-101"]["level"], MatchLevel.CRITICAL)
        self.assertEqual(by_key["01-F001-102"]["level"], MatchLevel.REVIEW)
        self.assertEqual(by_key["01-F001-103"]["status"], DocMatchStatus.SIRE_ONLY)
        self.assertEqual(by_key["01-F001-104"]["status"], DocMatchStatus.AMOUNT_MISMATCH)
        self.assertAlmostEqual(by_key["01-F001-104"]["differences"]["amount_diff"], 100.0)
        self.assertEqual(by_key["01-F001-105"]["level"], MatchLevel.OK)
        # las notas de crédito restan en los totales
        cpe("200", "118.00", klass=DocumentClass.CREDIT_NOTE, doc_type="07")
        totals = cpe_sire.reconcile_direction(RUC, PERIOD, "sales")["totals"]
        self.assertEqual(totals["cpe_total"], Decimal("1180.00") + Decimal("2360.00") + Decimal("590.00") + Decimal("1000.00") - Decimal("118.00"))

    def test_boleta_absent_is_only_a_warning(self):
        cpe("300", "5000.00", doc_type="03", series="B001")
        rows = cpe_sire.reconcile_direction(RUC, PERIOD, "sales")["rows"]
        row = next(r for r in rows if r["doc_key"].startswith("03-"))
        self.assertEqual(row["level"], MatchLevel.WARNING)
        self.assertIn("consolidadas", row["differences"]["notes"][0])

    def test_other_company_sees_sire_unavailable(self):
        sire_sale("900", "100.00")
        result = cpe_sire.reconcile_direction("20100000009", PERIOD, "sales")
        self.assertFalse(result["totals"]["sire_available"])
        self.assertEqual(result["rows"], [])


@override_settings(SUNAT={"RUC": RUC})
class MatchingTests(TenantAPITestCase):
    RUC = RUC

    def mov(self, amount, *, date=datetime.date(2026, 7, 20), desc="", kind=MovementKind.CREDIT, account="191-111"):
        return BankMovement.objects.create(
            account_ruc=RUC, date=date, period=f"{date.year}{date.month:02d}", kind=kind,
            amount=Decimal(amount), description=desc, bank="BCP", bank_account=account,
        )

    def test_one_invoice_one_exact_payment(self):
        inv = cpe("400", "10000.00")
        self.mov("10000.00", desc="TRANSFERENCIA CLIENTE ANDES SAC")
        matching.rebuild_settlements(RUC)
        st = InvoiceSettlement.objects.get(invoice=inv)
        self.assertEqual(st.status, SettlementStatus.PAID)
        line = st.lines.get()
        self.assertGreaterEqual(line.confidence, 0.8)
        self.assertTrue(any("Razón social" in e for e in line.evidence))

    def test_partial_payments_accumulate(self):
        inv = cpe("401", "10000.00")
        self.mov("3000.00", desc="PAGO PARCIAL CLIENTE ANDES", date=datetime.date(2026, 7, 15))
        self.mov("7000.00", desc="CANCELACION CLIENTE ANDES", date=datetime.date(2026, 8, 2))
        matching.rebuild_settlements(RUC)
        st = InvoiceSettlement.objects.get(invoice=inv)
        self.assertEqual(st.status, SettlementStatus.PAID)
        self.assertEqual(st.lines.count(), 2)
        self.assertEqual(st.collection_period, "202608")   # facturación 202607, cobranza 202608
        self.assertEqual(st.billing_period, "202607")

    def test_partial_only_leaves_balance(self):
        inv = cpe("402", "10000.00")
        self.mov("6000.00", desc="ABONO CLIENTE ANDES")
        matching.rebuild_settlements(RUC)
        st = InvoiceSettlement.objects.get(invoice=inv)
        self.assertEqual(st.status, SettlementStatus.PARTIAL)
        self.assertEqual(st.balance, Decimal("4000.00"))

    def test_one_payment_settles_two_invoices(self):
        a, b = cpe("403", "3000.00"), cpe("404", "7000.00")
        self.mov("10000.00", desc="PAGO FACTURAS CLIENTE ANDES SAC")
        matching.rebuild_settlements(RUC)
        self.assertEqual(InvoiceSettlement.objects.get(invoice=a).status, SettlementStatus.PAID)
        self.assertEqual(InvoiceSettlement.objects.get(invoice=b).status, SettlementStatus.PAID)

    def test_unrelated_credit_stays_unassigned(self):
        inv = cpe("405", "9999.00")
        self.mov("5432.10", desc="DEPOSITO EN EFECTIVO")
        matching.rebuild_settlements(RUC)
        self.assertEqual(InvoiceSettlement.objects.get(invoice=inv).status, SettlementStatus.UNPAID)


class BankingRulesTests(TenantAPITestCase):
    RUC = RUC

    def mov(self, amount, desc, *, kind=MovementKind.DEBIT, date=datetime.date(2026, 7, 5), account="191-111"):
        return BankMovement.objects.create(
            account_ruc=RUC, date=date, period="202607", kind=kind, amount=Decimal(amount),
            description=desc, bank_account=account,
        )

    def test_rule_based_categories_with_evidence(self):
        casos = {
            "PAGO SUNAT NPS 621": MovementCategory.TAX_PAYMENT,
            "PAGO PLANILLA JULIO": MovementCategory.PAYROLL_PAYMENT,
            "DESEMBOLSO PRESTAMO BCP": MovementCategory.LOAN,
            "APORTE DE CAPITAL SOCIO": MovementCategory.CAPITAL_CONTRIBUTION,
            "DEVOLUCION GARANTIA": MovementCategory.REFUND,
        }
        movs = {desc: self.mov("100.00", desc) for desc in casos}
        banking.classify_movements(RUC)
        for desc, cat in casos.items():
            movs[desc].refresh_from_db()
            self.assertEqual(movs[desc].category, cat, desc)
            self.assertTrue(movs[desc].evidence)
            self.assertEqual(movs[desc].classified_by, "rules")

    def test_own_transfer_detected_by_mirror(self):
        out = self.mov("20000.00", "TRANSFERENCIA", kind=MovementKind.DEBIT, account="191-111")
        inc = self.mov("20000.00", "TRANSFERENCIA", kind=MovementKind.CREDIT, account="194-222")
        banking.classify_movements(RUC)
        out.refresh_from_db(); inc.refresh_from_db()
        self.assertEqual(out.category, MovementCategory.OWN_ACCOUNT_TRANSFER)
        self.assertEqual(inc.category, MovementCategory.OWN_ACCOUNT_TRANSFER)

    def test_user_decision_is_never_overwritten(self):
        m = self.mov("500.00", "PAGO SUNAT")
        m.category = MovementCategory.OTHER; m.classified_by = "user"; m.save()
        banking.classify_movements(RUC)
        m.refresh_from_db()
        self.assertEqual(m.category, MovementCategory.OTHER)

    def test_unidentified_credit_counts_as_pending(self):
        self.mov("12500.00", "DEPOSITO VENTANILLA", kind=MovementKind.CREDIT)
        banking.classify_movements(RUC)
        self.assertEqual(banking.pending_amount(RUC, "202607"), Decimal("12500.00"))


@override_settings(SUNAT={"RUC": RUC})
class FullRunTests(TenantAPITestCase):
    RUC = RUC

    def test_full_run_persists_documents_alerts_and_score(self):
        cpe("500", "11800.00"); sire_sale("500", "11800.00", igv=Decimal("1800.00"))
        cpe("501", "5900.00")                          # CPE sin SIRE
        DeclaredSummary.objects.create(
            account_ruc=RUC, period=PERIOD, sales_base=Decimal("5000.00"),
            purchases_base=Decimal("0"), sales_igv=Decimal("900.00"), source="manual",
        )  # SIRE base > declarado → hallazgo
        BankMovement.objects.create(account_ruc=RUC, date=datetime.date(2026, 7, 12), period=PERIOD,
                                    kind=MovementKind.CREDIT, amount=Decimal("9000.00"), description="DEPOSITO")
        run = runner.run_reconciliation(RUC, PERIOD)
        self.assertEqual(run.status, "done")
        self.assertTrue(DocumentReconciliation.objects.filter(run=run).exists())
        kinds = set(FinanceAlert.objects.filter(account_ruc=RUC, dedup_key__startswith="recon:").values_list("alert_type", flat=True))
        self.assertIn("recon_cpe_not_in_sire", kinds)
        self.assertIn("recon_bank_credits_unclassified", kinds)
        score = ConsistencyScore.objects.get(account_ruc=RUC, period=PERIOD)
        self.assertLess(score.score, 100)
        self.assertTrue(score.breakdown)
        self.assertEqual(run.totals["score"], score.score)
        self.assertIsNotNone(run.totals["sales_sire"])

    def test_justified_alert_stops_hurting_the_score(self):
        cpe("600", "20000.00")   # CPE sin SIRE → hallazgo fuerte
        sire_sale("999", "1.00") # que el SIRE cuente como disponible
        run1 = runner.run_reconciliation(RUC, PERIOD)
        s1 = ConsistencyScore.objects.get(account_ruc=RUC, period=PERIOD).score
        alert = FinanceAlert.objects.get(account_ruc=RUC, dedup_key=f"recon:CPE_NOT_IN_SIRE:{PERIOD}")
        alert.status = AlertStatus.JUSTIFIED; alert.save()
        runner.run_reconciliation(RUC, PERIOD)
        s2 = ConsistencyScore.objects.get(account_ruc=RUC, period=PERIOD).score
        self.assertGreater(s2, s1)
        alert.refresh_from_db(); self.assertEqual(alert.status, AlertStatus.JUSTIFIED)

    def test_without_sire_nothing_blames_the_company(self):
        cpe("700", "8000.00")
        run = runner.run_reconciliation(RUC, PERIOD)
        self.assertFalse(run.totals["sire_available"])
        self.assertFalse(FinanceAlert.objects.filter(account_ruc=RUC, dedup_key__startswith="recon:CPE_NOT_IN_SIRE").exists())
