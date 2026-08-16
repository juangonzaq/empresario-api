"""Tests for the executive finance analytics."""

from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase  # noqa: F401

from core.testing import TenantAPITestCase

from sunat_cpe.models import Direction, DocumentClass, ElectronicInvoice
from sunat_itf.models import ItfRecord

from finance_analytics.models import FinanceAiSummary, FinanceAlert
from finance_analytics.services import ai_summary, parties
from finance_analytics.services.alerts import rebuild_alerts
from finance_analytics.services.consistency import consistency_analysis
from finance_analytics.services.cpe_summary import (
    load_documents, sales_summary,
)
from finance_analytics.services.itf_summary import itf_summary
from finance_analytics.services.xml_extract import fix_mojibake, parse_invoice_xml

RUC = "20604442533"

UBL_INVOICE = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
  <cbc:ID>E001-100</cbc:ID>
  <cbc:DocumentCurrencyCode>PEN</cbc:DocumentCurrencyCode>
  <cac:PaymentTerms><cbc:ID>FormaPago</cbc:ID><cbc:PaymentMeansID>Credito</cbc:PaymentMeansID></cac:PaymentTerms>
  <cac:PaymentTerms><cbc:ID>FormaPago</cbc:ID><cbc:PaymentMeansID>Cuota001</cbc:PaymentMeansID>
    <cbc:Amount currencyID="PEN">590.00</cbc:Amount><cbc:PaymentDueDate>2026-08-15</cbc:PaymentDueDate></cac:PaymentTerms>
  <cac:TaxTotal><cbc:TaxAmount currencyID="PEN">90.00</cbc:TaxAmount>
    <cac:TaxSubtotal><cbc:TaxableAmount currencyID="PEN">500.00</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="PEN">90.00</cbc:TaxAmount>
      <cac:TaxCategory><cac:TaxScheme><cbc:ID>1000</cbc:ID><cbc:Name>IGV</cbc:Name></cac:TaxScheme></cac:TaxCategory>
    </cac:TaxSubtotal></cac:TaxTotal>
  <cac:LegalMonetaryTotal><cbc:PayableAmount currencyID="PEN">590.00</cbc:PayableAmount></cac:LegalMonetaryTotal>
  <cac:InvoiceLine><cbc:InvoicedQuantity>2.00</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="PEN">500.00</cbc:LineExtensionAmount>
    <cac:Item><cbc:Description>SERVICIO DE DISEÃ‘O</cbc:Description></cac:Item></cac:InvoiceLine>
</Invoice>"""


def make_doc(**overrides):
    defaults = {
        "account_ruc": RUC,
        "direction": Direction.ISSUED,
        "document_class": DocumentClass.INVOICE,
        "document_type": "10",
        "issuer_ruc": RUC,
        "series": "E001",
        "number": str(make_doc.counter),
        "full_number": f"E001-{make_doc.counter}",
        "period": "202607",
        "currency": "PEN",
        "total_amount": Decimal("1000.00"),
        "receiver_ruc": "20100000001",
        "receiver_name": "CLIENTE UNO S.A.C.",
    }
    make_doc.counter += 1
    return ElectronicInvoice.objects.create(**{**defaults, **overrides})


make_doc.counter = 1


class XmlExtractTests(TenantAPITestCase):
    def test_parse_invoice_xml(self):
        data = parse_invoice_xml(UBL_INVOICE)
        self.assertEqual(data["currency"], "PEN")
        self.assertEqual(data["total_amount"], Decimal("590.00"))
        self.assertEqual(data["taxable_amount"], Decimal("500.00"))
        self.assertEqual(data["igv_amount"], Decimal("90.00"))
        self.assertEqual(data["payment_form"], "Credito")
        self.assertEqual(data["installments"][0]["due_date"], "2026-08-15")
        # Mojibake in item descriptions is repaired.
        self.assertEqual(data["items"][0]["description"], "SERVICIO DE DISEÑO")

    def test_fix_mojibake(self):
        self.assertEqual(fix_mojibake("COMPAÃ\x91IA"), "COMPAÑIA")
        self.assertEqual(fix_mojibake("COMPAÑÍA"), "COMPAÑÍA")


@override_settings(SUNAT_RUC=RUC)
class SalesSummaryTests(TenantAPITestCase):
    def test_credit_notes_reduce_and_currencies_stay_apart(self):
        make_doc(total_amount=Decimal("1000"))
        make_doc(total_amount=Decimal("500"))
        make_doc(
            document_class=DocumentClass.CREDIT_NOTE,
            total_amount=Decimal("200"),
            references_document="E001-1",
        )
        make_doc(currency="USD", total_amount=Decimal("300"))
        make_doc(direction=Direction.RECEIVED, issuer_ruc="20222222222",
                 issuer_name="PROVEEDOR SAC", total_amount=Decimal("999"))
        make_doc(is_cancelled=True, total_amount=Decimal("5000"))

        sales = sales_summary(load_documents(RUC), months=1)
        pen = sales["current"]["by_currency"]["PEN"]
        usd = sales["current"]["by_currency"]["USD"]
        self.assertEqual(pen["gross"], 1500.0)
        self.assertEqual(pen["credit_notes"], 200.0)
        self.assertEqual(pen["net"], 1300.0)
        self.assertEqual(usd["net"], 300.0)          # nunca sumado al PEN
        self.assertEqual(sales["current"]["cancelled"], 1)
        # La compra recibida no aparece en ventas.
        self.assertEqual(pen["invoice_count"], 2)

    def test_variation_compares_net_against_net(self):
        # jun: bruto 2000, NC 1000 → neto 1000. jul: bruto 1200, sin NC → 1200.
        make_doc(period="202606", total_amount=Decimal("2000"))
        make_doc(period="202606", document_class=DocumentClass.CREDIT_NOTE,
                 total_amount=Decimal("1000"))
        make_doc(period="202607", total_amount=Decimal("1200"))

        current = sales_summary(load_documents(RUC), months=2)["current"]
        self.assertEqual(current["net_pen"], 1200.0)
        self.assertEqual(current["previous_period"], "202606")
        self.assertEqual(current["previous_net_pen"], 1000.0)
        # +20% (neto vs neto). Contra el bruto anterior daría -40%.
        self.assertEqual(current["variation_pen_pct"], 20.0)
        self.assertIn("neto", current["variation_basis"])

    def test_empty_month_is_not_skipped_when_comparing(self):
        make_doc(period="202605", total_amount=Decimal("1000"))
        make_doc(period="202607", total_amount=Decimal("3000"))
        current = sales_summary(load_documents(RUC), months=3)["current"]
        # El mes anterior real (junio) es 0, no se arrastra el neto de mayo.
        self.assertEqual(current["previous_period"], "202606")
        self.assertEqual(current["previous_net_pen"], 0.0)
        self.assertIsNone(current["variation_pen_pct"])


@override_settings(SUNAT_RUC=RUC)
class CustomerAnalysisTests(TenantAPITestCase):
    def test_concentration_and_status(self):
        for period in ("202605", "202606", "202607"):
            make_doc(period=period, total_amount=Decimal("800"),
                     receiver_ruc="20100000001", receiver_name="CLIENTE UNO S.A.C.")
        make_doc(period="202607", total_amount=Decimal("200"),
                 receiver_ruc="20300000003", receiver_name="CLIENTE NUEVO EIRL")

        data = parties.customers_analysis(load_documents(RUC), months=3)
        top = data["summary"]["top"]
        self.assertEqual(top["name"], "CLIENTE UNO S.A.C.")
        self.assertAlmostEqual(top["share_pct"], 92.3, places=1)
        self.assertIsNotNone(data["summary"]["concentration_message"])
        statuses = {r["name"]: r["status"] for r in data["parties"]}
        self.assertEqual(statuses["CLIENTE UNO S.A.C."], "recurrente")
        self.assertEqual(statuses["CLIENTE NUEVO EIRL"], "nuevo")

    def test_window_is_always_reported_and_defaults_to_12_months(self):
        make_doc(period="202607", total_amount=Decimal("1000"))
        summary = parties.customers_analysis(load_documents(RUC))["summary"]
        self.assertEqual(summary["window"]["months"], 12)
        self.assertEqual(summary["window"]["start"], "202508")
        self.assertEqual(summary["window"]["end"], "202607")
        self.assertEqual(summary["window"]["label"], "ago 2025 – jul 2026")
        self.assertIn("neta", summary["share_basis"])

    def test_concentration_matches_net_sales_of_the_window(self):
        make_doc(period="202607", total_amount=Decimal("1000"),
                 receiver_ruc="20100000001", receiver_name="CLIENTE UNO S.A.C.")
        make_doc(period="202607", document_class=DocumentClass.CREDIT_NOTE,
                 total_amount=Decimal("400"),
                 receiver_ruc="20100000001", receiver_name="CLIENTE UNO S.A.C.")
        docs = load_documents(RUC)
        summary = parties.customers_analysis(docs)["summary"]
        net_pen = sales_summary(docs, months=12)["current"]["net_pen"]
        # El denominador de la concentración es el mismo neto del card.
        self.assertEqual(summary["total_net_pen"], net_pen)
        self.assertEqual(summary["top"]["share_pct"], 100.0)
        self.assertIn("últimos 12 meses", summary["concentration_message"])


@override_settings(SUNAT_RUC=RUC)
class ItfDirectionTests(TenantAPITestCase):
    """Códigos 12/13 = acreditaciones (entradas); 14/15 = débitos (salidas)."""

    def _record(self, period, code, base):
        return ItfRecord.objects.create(
            taxpayer_id=RUC, section="accumulated", period=period,
            declarant_name="BANCO DE CREDITO DEL PERU", operation_code=code,
            base_amount=Decimal(base), tax=Decimal("0.50"),
        )

    def test_codes_split_into_inflows_and_outflows(self):
        self._record("202607", "12", "1000")
        self._record("202607", "13", "500")
        self._record("202607", "14", "200")
        self._record("202607", "15", "300")
        self._record("202607", "99", "70")   # fuera de catálogo

        current = itf_summary(RUC)["current"]
        self.assertEqual(current["inflow_base"], 1500.0)
        self.assertEqual(current["outflow_base"], 500.0)
        self.assertEqual(current["unclassified_base"], 70.0)
        # El movimiento bruto es la suma de ambos sentidos: dato secundario.
        self.assertEqual(current["gross_movement"], 2070.0)
        self.assertEqual(current["total_tax"], 2.5)

    def test_padded_codes_are_normalized(self):
        self._record("202607", "012", "800")
        self.assertEqual(itf_summary(RUC)["current"]["inflow_base"], 800.0)
        self.assertEqual(itf_summary(RUC)["unclassified_codes"], [])

    def test_variations_are_per_direction_and_named(self):
        self._record("202606", "12", "1000")
        self._record("202606", "14", "1000")
        self._record("202607", "12", "2000")   # entradas +100%
        self._record("202607", "14", "1000")   # salidas sin cambio

        current = itf_summary(RUC)["current"]
        self.assertEqual(current["variation_inflow_pct"], 100.0)
        self.assertEqual(current["variation_outflow_pct"], 0.0)
        self.assertEqual(current["variation_gross_pct"], 50.0)
        # La variación atípica declara siempre qué concepto comparó.
        self.assertTrue(current["atypical"])
        self.assertEqual(current["atypical_basis"], "inflow")
        self.assertIn("entradas", current["atypical_basis_label"])


@override_settings(SUNAT_RUC=RUC)
class ConsistencyAndAlertTests(TenantAPITestCase):
    def setUp(self):
        make_doc(period="202606", total_amount=Decimal("1000"))
        ItfRecord.objects.create(
            taxpayer_id=RUC, section="accumulated", period="202606",
            declarant_name="BANCO DE CREDITO DEL PERU", operation_code="15",
            base_amount=Decimal("50000"), tax=Decimal("2.50"),
        )

    def test_gap_is_review_note_never_accusation(self):
        data = consistency_analysis(load_documents(RUC), RUC, months=2)
        self.assertEqual(data["status"], "requiere_revision")
        gap = next(f for f in data["findings"] if f["kind"] == "itf_outflow_without_cpe")
        self.assertIn("clasificación o revisión contable", gap["recommendation"])
        self.assertFalse(gap["is_breach"])
        text = str(data).lower()
        for banned in ("evasión", "fraude", "omisión", "no declarado"):
            self.assertNotIn(banned, text)
        # "incumplimiento" solo aparece negado, y ningún hallazgo lo afirma.
        self.assertIn("no es un incumplimiento", text)
        self.assertTrue(all(not f["is_breach"] for f in data["findings"]))

    def test_cross_compares_matching_directions(self):
        row = consistency_analysis(load_documents(RUC), RUC, months=2)["rows"][-1]
        # El débito de 50k NO se compara contra la facturación emitida.
        self.assertEqual(row["itf_outflow"], 50000.0)
        self.assertEqual(row["itf_inflow"], 0.0)
        # Cero acreditaciones frente a la facturación del mes, no "sin dato".
        self.assertEqual(row["inflow_vs_sales_ratio"], 0.0)
        self.assertIsNone(row["outflow_vs_received_ratio"])

    def test_unclassified_codes_are_informational_not_findings(self):
        ItfRecord.objects.create(
            taxpayer_id=RUC, section="accumulated", period="202606",
            declarant_name="BANCO", operation_code="77",
            base_amount=Decimal("900"), tax=Decimal("0"),
        )
        data = consistency_analysis(load_documents(RUC), RUC, months=2)
        pending = next(
            f for f in data["findings"] if f["kind"] == "itf_unclassified_movements"
        )
        self.assertEqual(pending["classification"], "informativo")
        self.assertFalse(pending["is_breach"])
        # No suma a los hallazgos que exigen revisión.
        self.assertEqual(
            data["review_findings"],
            len([f for f in data["findings"] if f["classification"] == "requiere_revision"]),
        )

    def test_alerts_upsert_and_keep_human_status(self):
        stats = rebuild_alerts(RUC)
        self.assertGreaterEqual(stats["total"], 1)
        alert = FinanceAlert.objects.filter(alert_type="itf_outflow_without_cpe").first()
        self.assertIsNotNone(alert)
        alert.status = "descartada"
        alert.save()
        rebuild_alerts(RUC)
        alert.refresh_from_db()
        self.assertEqual(alert.status, "descartada")
        self.assertEqual(
            FinanceAlert.objects.filter(alert_type="itf_outflow_without_cpe").count(), 1
        )


@override_settings(SUNAT_RUC=RUC)
class ApiTests(TenantAPITestCase):
    def setUp(self):
        self.invoice = make_doc(total_amount=Decimal("590"))
        from finance_analytics.services.xml_extract import extract_invoice
        self.invoice.xml_content = UBL_INVOICE
        self.invoice.save()
        extract_invoice(self.invoice)

    def test_invoice_insight_has_no_full_xml(self):
        url = reverse("finance_analytics:invoice-insight", args=[self.invoice.id])
        data = self.client.get(url).data
        self.assertEqual(data["extract"]["igv_amount"], 90.0)
        self.assertNotIn("xml_content", str(data))

    def test_period_documents_match_the_table_row(self):
        make_doc(period="202607", total_amount=Decimal("1000"))
        make_doc(period="202607", document_class=DocumentClass.CREDIT_NOTE,
                 total_amount=Decimal("300"))
        make_doc(period="202607", total_amount=Decimal("5000"), is_cancelled=True)
        make_doc(period="202607", currency="USD", total_amount=Decimal("400"))
        make_doc(period="202606", total_amount=Decimal("900"))

        url = reverse("finance_analytics:period-documents")
        data = self.client.get(
            url, {"period": "202607", "direction": "emitida", "currency": "PEN"}
        ).data

        # El total del detalle es el mismo que muestra la fila que lo abrió.
        row = sales_summary(load_documents(RUC), months=1)["current"]
        self.assertEqual(data["totals"]["net"], row["by_currency"]["PEN"]["net"])
        self.assertEqual(data["cancelled"], 1)
        # El anulado se lista (marcado), la nota de crédito se marca como resta
        # y el comprobante en dólares queda fuera de esta moneda.
        self.assertEqual(len(data["documents"]), 4)
        self.assertTrue(any(d["is_cancelled"] for d in data["documents"]))
        self.assertTrue(any(d["subtracts"] for d in data["documents"]))
        self.assertTrue(all(d["currency"] == "PEN" for d in data["documents"]))

    def test_period_documents_reject_bad_input(self):
        url = reverse("finance_analytics:period-documents")
        self.assertEqual(self.client.get(url, {"period": "07"}).status_code, 400)
        self.assertEqual(
            self.client.get(
                url, {"period": "202607", "direction": "inventada"}
            ).status_code,
            400,
        )

    def test_overview_works_for_a_brand_new_company(self):
        """Toda empresa empieza sin un solo comprobante: el panel debe abrir."""
        ElectronicInvoice.objects.all().delete()
        response = self.client.get(reverse("finance_analytics:overview"))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["period"])
        self.assertIsNone(response.data["sales"]["current"])
        self.assertIsNone(response.data["sales"]["previous"])
        self.assertEqual(response.data["alerts"]["total"], 0)

    def test_overview_endpoint(self):
        data = self.client.get(reverse("finance_analytics:overview")).data
        self.assertEqual(data["period"], "202607")
        self.assertIn("PEN", data["sales"]["current"]["by_currency"])
        self.assertIn("alerts", data)

    def test_alert_status_patch(self):
        rebuild_alerts(RUC)
        alert = FinanceAlert.objects.first()
        if alert is None:  # dataset mínimo puede no generar alertas
            self.skipTest("sin alertas en el dataset mínimo")
        url = reverse("finance_analytics:alert-status", args=[alert.id])
        response = self.client.patch(url, {"status": "resuelta"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "resuelta")


@override_settings(SUNAT_RUC=RUC)
class BriefingTests(TenantAPITestCase):
    """El briefing ejecutivo: recortes, limpieza, fechas y estados."""

    RAW = {
        "summary": " ".join(["palabra"] * 90),
        "key_changes": ["Sube la facturación", "Bajan las salidas", "Nuevo cliente", "Sobra"],
        "attention": [
            {"title": f"Punto {i}", "detail": "Detalle"} for i in range(5)
        ],
        "actions": [
            {"action": "Revisar cobranzas", "owner": "Contabilidad",
             "due_in_days": 7, "why": "Para ordenar el mes"},
            {"action": "Llamar al cliente principal", "owner": "Comercial",
             "due_in_days": 400, "why": "Reducir dependencia"},
            {"action": "Clasificar movimientos", "owner": "Tesorería",
             "due_in_days": 3, "why": "Cerrar el cruce"},
            {"action": "Acción de más", "owner": "Gerencia",
             "due_in_days": 5, "why": "Sobra"},
        ],
    }

    def _shape(self, raw=None, previous=None):
        return ai_summary._shape(raw or self.RAW, "202607", previous or [])

    def test_caps_summary_and_blocks(self):
        shaped = self._shape()
        self.assertEqual(len(shaped["summary"].split()), ai_summary.SUMMARY_WORD_LIMIT)
        self.assertEqual(len(shaped["key_changes"]), 3)
        self.assertEqual(len(shaped["attention"]), 3)
        self.assertEqual(len(shaped["actions"]), 3)

    def test_actions_carry_owner_date_and_status(self):
        action = self._shape()["actions"][0]
        self.assertEqual(action["owner"], "Contabilidad")
        self.assertEqual(action["status"], "sugerida")
        expected = timezone.localdate() + datetime.timedelta(days=7)
        self.assertEqual(action["due_date"], expected.isoformat())

    def test_due_date_is_clamped(self):
        action = self._shape()["actions"][1]
        limit = timezone.localdate() + datetime.timedelta(days=ai_summary.MAX_DUE_DAYS)
        self.assertEqual(action["due_date"], limit.isoformat())

    def test_person_set_status_survives_regeneration(self):
        first = self._shape()
        first["actions"][0]["status"] = "hecha"
        again = self._shape(previous=first["actions"])
        self.assertEqual(again["actions"][0]["status"], "hecha")
        self.assertEqual(again["actions"][2]["status"], "sugerida")

    def test_internal_names_and_currency_are_cleaned(self):
        raw = {
            **self.RAW,
            "summary": "El net_pen creció y el cruce cpe_itf_gap sigue abierto; "
                       "PEN 12,300 en notas y S/. 400 en ajustes según el XML.",
        }
        summary = self._shape(raw)["summary"]
        for jargon in ("net_pen", "cpe_itf_gap", "PEN 12", "S/.", "XML"):
            self.assertNotIn(jargon, summary)
        self.assertIn("S/ 12,300", summary)

    def test_action_status_endpoint(self):
        row = FinanceAiSummary.objects.create(
            account_ruc=RUC, period="202607", fingerprint="x",
            summary="Lectura", key_changes=[], attention=[],
            actions=self._shape()["actions"],
        )
        action_id = row.actions[0]["id"]
        url = reverse("finance_analytics:ai-summary-action", args=[action_id])
        response = self.client.patch(url, {"status": "en_curso"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["actions"][0]["status"], "en_curso")
        self.assertEqual(
            self.client.patch(url, {"status": "inventado"}, format="json").status_code,
            400,
        )

    def test_failed_generation_falls_back_to_last_briefing(self):
        make_doc(total_amount=Decimal("1000"))
        FinanceAiSummary.objects.create(
            account_ruc=RUC, period="202606", fingerprint="old",
            summary="Briefing anterior", key_changes=["Algo"], attention=[], actions=[],
        )
        with patch.object(
            ai_summary.llm, "structured_completion", side_effect=RuntimeError("boom")
        ):
            response = self.client.post(reverse("finance_analytics:ai-summary"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["stale"])
        self.assertEqual(response.data["summary"], "Briefing anterior")
        self.assertIn("último disponible", response.data["stale_note"])

    def test_failed_generation_without_history_reports_the_error(self):
        make_doc(total_amount=Decimal("1000"))
        with patch.object(
            ai_summary.llm, "structured_completion", side_effect=RuntimeError("boom")
        ):
            response = self.client.post(reverse("finance_analytics:ai-summary"))
        self.assertEqual(response.status_code, 502)

    def test_legacy_row_without_blocks_is_not_a_briefing(self):
        make_doc(total_amount=Decimal("1000"))
        FinanceAiSummary.objects.create(
            account_ruc=RUC, period="202607", fingerprint="viejo",
            summary="Resumen del formato anterior",
            key_changes=[], attention=[], actions=[],
        )
        overview = self.client.get(reverse("finance_analytics:overview")).data
        # Sin bloques no hay briefing: la vista invita a generarlo.
        self.assertIsNone(overview["ai_summary"])

    def test_overview_exposes_sources(self):
        make_doc(total_amount=Decimal("1000"))
        ItfRecord.objects.create(
            taxpayer_id=RUC, section="accumulated", period="202607",
            declarant_name="BANCO DE CREDITO", operation_code="12",
            base_amount=Decimal("500"), tax=Decimal("0.05"),
        )
        sources = self.client.get(reverse("finance_analytics:overview")).data["sources"]
        labels = [s["label"] for s in sources]
        self.assertIn("Comprobantes electrónicos", labels)
        self.assertIn("Movimientos bancarios reportados", labels)
        self.assertIn("BANCO DE CREDITO", str(sources))


@override_settings(SUNAT_RUC=RUC)
class ManualEntryTests(TenantAPITestCase):
    """Registros manuales: suman al total del mes y ninguna corrida los toca."""

    def _create(self, **overrides):
        payload = {
            "direction": "emitida",
            "entry_date": "2026-07-10",
            "description": "Venta al contado sin comprobante",
            "amount": "250.50",
            **overrides,
        }
        return self.client.post(
            reverse("finance_analytics:manual-entries"), payload, format="json"
        )

    def test_monthly_total_includes_manual_and_automatic(self):
        make_doc(period="202607", total_amount=Decimal("1000"))
        self._create()

        data = self.client.get(reverse("finance_analytics:sales")).data
        bucket = data["current"]["by_currency"]["PEN"]
        self.assertEqual(bucket["manual"], 250.5)
        self.assertEqual(bucket["manual_count"], 1)
        # El neto del mes = comprobantes + registros manuales.
        self.assertEqual(bucket["net"], 1250.5)

    def test_manual_expense_goes_to_purchases_not_sales(self):
        make_doc(period="202607", total_amount=Decimal("1000"))
        self._create(direction="recibida", description="Compra de utiles")

        sales = self.client.get(reverse("finance_analytics:sales")).data
        purchases = self.client.get(reverse("finance_analytics:purchases")).data
        self.assertEqual(sales["current"]["by_currency"]["PEN"]["manual"], 0)
        self.assertEqual(
            purchases["current"]["by_currency"]["PEN"]["manual"], 250.5
        )

    def test_month_with_only_manual_entries_appears(self):
        response = self._create()
        self.assertEqual(response.status_code, 201)
        data = self.client.get(reverse("finance_analytics:sales")).data
        self.assertEqual(data["latest_period"], "202607")
        self.assertEqual(data["current"]["by_currency"]["PEN"]["net"], 250.5)

    def test_period_documents_list_manual_entries_and_totals_match(self):
        make_doc(period="202607", total_amount=Decimal("1000"))
        self._create()
        data = self.client.get(
            reverse("finance_analytics:period-documents"),
            {"period": "202607", "direction": "emitida", "currency": "PEN"},
        ).data
        self.assertEqual(len(data["manual_entries"]), 1)
        self.assertEqual(data["manual_entries"][0]["origin"], "manual")
        self.assertEqual(data["manual_entries"][0]["kind"], "ingreso")
        self.assertEqual(data["totals"]["net"], 1250.5)
        # Los comprobantes de SUNAT se distinguen de lo manual.
        self.assertTrue(all(d["origin"] == "sunat" for d in data["documents"]))

    def test_patch_moves_period_and_delete_removes_from_totals(self):
        entry_id = self._create().data["id"]
        patched = self.client.patch(
            reverse("finance_analytics:manual-entry", args=[entry_id]),
            {"entry_date": "2026-06-05", "amount": "100"},
            format="json",
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.data["period"], "202606")
        self.assertEqual(patched.data["amount"], 100.0)

        deleted = self.client.delete(
            reverse("finance_analytics:manual-entry", args=[entry_id])
        )
        self.assertEqual(deleted.status_code, 204)
        data = self.client.get(reverse("finance_analytics:sales")).data
        self.assertIsNone(data["current"])

    def test_rejects_bad_input(self):
        self.assertEqual(self._create(amount="-5").status_code, 400)
        self.assertEqual(self._create(amount="no").status_code, 400)
        self.assertEqual(self._create(direction="inventada").status_code, 400)
        self.assertEqual(self._create(entry_date="julio").status_code, 400)
        self.assertEqual(self._create(description="  ").status_code, 400)


@override_settings(SUNAT_RUC=RUC)
class InvoiceOverrideTests(TenantAPITestCase):
    """Correcciones a comprobantes de SUNAT: se aplican al leer y las
    sincronizaciones no las chancan."""

    def setUp(self):
        self.invoice = make_doc(period="202607", total_amount=Decimal("1000"))
        self.url = reverse(
            "finance_analytics:invoice-override", args=[self.invoice.id]
        )

    def test_override_changes_totals_and_marks_row_as_edited(self):
        response = self.client.patch(
            self.url, {"total_amount": "800", "note": "Monto corregido"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        data = self.client.get(reverse("finance_analytics:sales")).data
        self.assertEqual(data["current"]["by_currency"]["PEN"]["net"], 800.0)

        docs = self.client.get(
            reverse("finance_analytics:period-documents"),
            {"period": "202607", "direction": "emitida", "currency": "PEN"},
        ).data
        self.assertTrue(docs["documents"][0]["edited"])
        self.assertEqual(docs["documents"][0]["amount"], 800.0)

    def test_override_survives_a_new_sync(self):
        self.client.patch(self.url, {"total_amount": "800"}, format="json")
        # La corrida diaria re-escribe el comprobante con lo que dice SUNAT.
        ElectronicInvoice.objects.update_or_create(
            account_ruc=self.invoice.account_ruc,
            issuer_ruc=self.invoice.issuer_ruc,
            document_type=self.invoice.document_type,
            series=self.invoice.series,
            number=self.invoice.number,
            defaults={"total_amount": Decimal("1000"), "period": "202607"},
        )
        data = self.client.get(reverse("finance_analytics:sales")).data
        self.assertEqual(data["current"]["by_currency"]["PEN"]["net"], 800.0)

    def test_delete_restores_sunat_values(self):
        self.client.patch(self.url, {"total_amount": "800"}, format="json")
        self.assertEqual(self.client.delete(self.url).status_code, 204)
        data = self.client.get(reverse("finance_analytics:sales")).data
        self.assertEqual(data["current"]["by_currency"]["PEN"]["net"], 1000.0)

    def test_insight_exposes_override_and_effective_total(self):
        self.client.patch(
            self.url, {"total_amount": "800", "counterparty": "CLIENTE REAL SAC"},
            format="json",
        )
        data = self.client.get(
            reverse("finance_analytics:invoice-insight", args=[self.invoice.id])
        ).data
        self.assertTrue(data["edited"])
        self.assertEqual(data["effective_total"], 800.0)
        self.assertEqual(data["override"]["counterparty"], "CLIENTE REAL SAC")
        # El total original de SUNAT sigue visible para auditoría.
        self.assertEqual(data["total_amount"], 1000.0)

    def test_empty_override_is_removed(self):
        self.client.patch(self.url, {"total_amount": "800"}, format="json")
        response = self.client.patch(
            self.url, {"total_amount": None, "note": ""}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data)
        data = self.client.get(
            reverse("finance_analytics:invoice-insight", args=[self.invoice.id])
        ).data
        self.assertFalse(data["edited"])


@override_settings(SUNAT_RUC=RUC)
class SemaforoTests(TenantAPITestCase):
    """El semáforo de gastos: personal, otros gastos y total sobre ingresos."""

    def _semaforo(self):
        return self.client.get(reverse("finance_analytics:overview")).data["semaforo"]

    def _fila(self, data, key):
        return next(r for r in data["rows"] if r["key"] == key)

    def test_calcula_los_tres_porcentajes_sobre_los_ingresos(self):
        from colaboradores.models import Colaborador

        make_doc(period="202607", total_amount=Decimal("10000"))
        make_doc(
            period="202607", direction=Direction.RECEIVED,
            issuer_ruc="20111111111", total_amount=Decimal("3000"),
        )
        Colaborador.objects.create(
            taxpayer_id=RUC, full_name="Juana Pérez", document_number="45678912",
            monthly_salary=Decimal("2700"),
        )

        data = self._semaforo()
        self.assertEqual(data["ingresos_pen"], 10000.0)

        # Personal = sueldo 2700 + EsSalud 9 % (243) = 2943 → 29.4 %.
        personal = self._fila(data, "personal")
        self.assertEqual(personal["amount_pen"], 2943.0)
        self.assertEqual(personal["pct"], 29.4)
        self.assertEqual(personal["estado"], "amarillo")
        self.assertEqual(
            [c["amount_pen"] for c in personal["breakdown"]], [2700.0, 243.0]
        )
        self.assertIn("EsSalud", personal["breakdown"][1]["label"])

        otros = self._fila(data, "otros")
        self.assertEqual(otros["pct"], 30.0)
        self.assertEqual(otros["estado"], "verde")

        total = self._fila(data, "total")
        self.assertEqual(total["pct"], 59.4)
        self.assertEqual(total["estado"], "verde")

    def test_amarillo_y_rojo_segun_umbral_de_personal(self):
        from colaboradores.models import Colaborador

        make_doc(period="202607", total_amount=Decimal("10000"))
        Colaborador.objects.create(
            taxpayer_id=RUC, full_name="Juana Pérez", document_number="45678912",
            monthly_salary=Decimal("3000"),  # +EsSalud = 3270 → 32.7 %: amarillo
        )
        self.assertEqual(self._fila(self._semaforo(), "personal")["estado"], "amarillo")

        Colaborador.objects.create(
            taxpayer_id=RUC, full_name="Pedro Rojas", document_number="87654321",
            monthly_salary=Decimal("2000"),  # juntos 5450 → 54.5 %: rojo
        )
        from finance_analytics import cache as overview_cache
        overview_cache.invalidate(RUC)
        self.assertEqual(self._fila(self._semaforo(), "personal")["estado"], "rojo")

    def test_essalud_respeta_el_piso_de_la_rmv(self):
        from colaboradores.models import Colaborador

        make_doc(period="202607", total_amount=Decimal("10000"))
        # Sueldo por debajo de la RMV (1130): el aporte se calcula sobre la
        # RMV, no sobre el sueldo registrado.
        Colaborador.objects.create(
            taxpayer_id=RUC, full_name="Juana Pérez", document_number="45678912",
            monthly_salary=Decimal("800"),
        )
        personal = self._fila(self._semaforo(), "personal")
        essalud = next(c for c in personal["breakdown"] if "EsSalud" in c["label"])
        self.assertEqual(essalud["amount_pen"], round(1130 * 0.09, 2))

    def test_los_gastos_manuales_entran_a_otros_gastos(self):
        make_doc(period="202607", total_amount=Decimal("10000"))
        self.client.post(
            reverse("finance_analytics:manual-entries"),
            {
                "direction": "recibida", "entry_date": "2026-07-05",
                "description": "Compra sin comprobante", "amount": "500",
            },
            format="json",
        )
        self.assertEqual(self._fila(self._semaforo(), "otros")["pct"], 5.0)

    def test_sin_ingresos_no_hay_base_y_se_dice(self):
        from colaboradores.models import Colaborador

        Colaborador.objects.create(
            taxpayer_id=RUC, full_name="Juana Pérez", document_number="45678912",
            monthly_salary=Decimal("2700"),
        )
        data = self._semaforo()
        self.assertEqual(self._fila(data, "personal")["estado"], "sin_base")
        self.assertTrue(any("Sin ingresos" in a for a in data["avisos"]))

    def test_avisa_de_sueldos_sin_registrar(self):
        from colaboradores.models import Colaborador

        make_doc(period="202607", total_amount=Decimal("10000"))
        Colaborador.objects.create(
            taxpayer_id=RUC, full_name="Juana Pérez", document_number="45678912",
        )
        data = self._semaforo()
        self.assertTrue(any("sin sueldo" in a for a in data["avisos"]))

    def test_compara_compras_del_mismo_mes_que_los_ingresos(self):
        # Ingresos en julio; compras solo en junio: «otros gastos» de julio es 0,
        # no el neto de junio.
        make_doc(period="202607", total_amount=Decimal("10000"))
        make_doc(
            period="202606", direction=Direction.RECEIVED,
            issuer_ruc="20111111111", total_amount=Decimal("9000"),
        )
        data = self._semaforo()
        self.assertEqual(data["period"], "202607")
        self.assertEqual(self._fila(data, "otros")["pct"], 0.0)
