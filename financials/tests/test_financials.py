"""Core dashboard tests: idempotent ingestion, the categorization
cascade, a hand-verified income statement, ratios and the masters API."""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.urls import reverse
from rest_framework import status as http

from core.testing import TenantAPITestCase
from sunat_cpe.models import Direction, DocumentClass, ElectronicInvoice

from financials.models import (
    CategorizationStatus, FinancialTransaction, TransactionCategory,
)
from financials.services import categorization, ingest, ratios, statements

RUC = "20604442533"


def make_invoice(**overrides) -> ElectronicInvoice:
    defaults = {
        "account_ruc": RUC,
        "direction": Direction.ISSUED,
        "document_class": DocumentClass.INVOICE,
        "document_type": "10",
        "issuer_ruc": RUC,
        "series": "E001",
        "number": str(make_invoice.counter),
        "full_number": f"E001-{make_invoice.counter}",
        "period": "202603",
        "issue_date": datetime.date(2026, 3, 10),
        "currency": "PEN",
        "total_amount": Decimal("1180.00"),
        "receiver_ruc": "20100000001",
        "receiver_name": "CLIENTE UNO S.A.C.",
    }
    make_invoice.counter += 1
    return ElectronicInvoice.objects.create(**{**defaults, **overrides})


make_invoice.counter = 1


class IngestTests(TenantAPITestCase):
    def test_sunat_ingestion_is_idempotent(self):
        make_invoice()
        first = ingest.ingest_sunat(RUC)
        second = ingest.ingest_sunat(RUC)
        self.assertEqual(first, {"created": 1, "updated": 0})
        self.assertEqual(second, {"created": 0, "updated": 1})
        self.assertEqual(FinancialTransaction.objects.count(), 1)

    def test_net_derives_from_master_igv_when_no_xml(self):
        make_invoice(total_amount=Decimal("1180.00"))
        ingest.ingest_sunat(RUC)
        row = FinancialTransaction.objects.get()
        # 1180 / 1.18 = 1000 net + 180 IGV, from the TaxRate master.
        self.assertEqual(row.net_amount_pen, Decimal("1000.00"))
        self.assertEqual(row.tax_amount_pen, Decimal("180.00"))

    def test_confirmed_category_survives_reingest(self):
        make_invoice()
        ingest.ingest_sunat(RUC)
        row = FinancialTransaction.objects.get()
        category = TransactionCategory.objects.get(taxpayer_id="", code="SALES_GROSS")
        categorization.confirm(row, category, None)
        ingest.ingest_sunat(RUC)
        row.refresh_from_db()
        self.assertEqual(row.categorization_status, CategorizationStatus.CONFIRMED)
        self.assertEqual(row.category.code, "SALES_GROSS")

    def test_counterparty_consolidates_by_ruc(self):
        make_invoice(receiver_name="CLIENTE UNO SAC")
        make_invoice(receiver_name="Cliente Uno S.A.C.")
        ingest.ingest_sunat(RUC)
        from financials.models import Counterparty

        self.assertEqual(
            Counterparty.objects.filter(taxpayer_id=RUC).count(), 1
        )


class CategorizationTests(TenantAPITestCase):
    def setUp(self):
        make_invoice()
        ingest.ingest_sunat(RUC)
        self.row = FinancialTransaction.objects.get()

    def test_issued_invoice_confirms_as_sale_on_sync(self):
        categorization.categorize(self.row)
        # Emitida = venta, confirmada apenas se sincroniza.
        self.assertEqual(self.row.categorization_status, CategorizationStatus.CONFIRMED)
        self.assertEqual(self.row.category.code, "SALES_GROSS")

    def test_received_invoice_confirms_as_purchase_on_sync(self):
        make_invoice(
            direction=Direction.RECEIVED, issuer_ruc="20111111111",
            total_amount=Decimal("590.00"),
        )
        ingest.ingest_sunat(RUC)
        categorization.categorize_pending(RUC)
        purchase = FinancialTransaction.objects.get(source="sunat_purchases")
        self.assertEqual(purchase.categorization_status, CategorizationStatus.CONFIRMED)
        self.assertEqual(purchase.category.code, "COST_OF_SALES")

    def test_received_credit_note_confirms_as_purchase_return(self):
        make_invoice(
            direction=Direction.RECEIVED, issuer_ruc="20111111111",
            document_class=DocumentClass.CREDIT_NOTE,
            total_amount=Decimal("118.00"),
        )
        ingest.ingest_sunat(RUC)
        categorization.categorize_pending(RUC)
        note = FinancialTransaction.objects.get(source="sunat_purchases")
        self.assertEqual(note.category.code, "PURCHASE_RETURNS")

    def test_confirmed_rows_stay_editable_via_bulk_categorize(self):
        categorization.categorize(self.row)  # queda CONFIRMED como venta
        response = self.client.post(
            reverse("financials:bulk-categorize"),
            {"ids": [str(self.row.id)], "category_code": "OTHER_INCOME",
             "create_rule": False},
            format="json",
        )
        self.assertEqual(response.status_code, http.HTTP_200_OK)
        self.row.refresh_from_db()
        self.assertEqual(self.row.category.code, "OTHER_INCOME")
        self.assertEqual(self.row.categorization_status, CategorizationStatus.CONFIRMED)

    def test_transactions_list_paginates_searches_and_filters(self):
        for _ in range(3):
            make_invoice()
        make_invoice(direction=Direction.RECEIVED, issuer_ruc="20111111111",
                     issuer_name="PROVEEDOR ANDINO S.A.")
        ingest.ingest_sunat(RUC)
        categorization.categorize_pending(RUC)
        url = reverse("financials:transactions")

        todo = self.client.get(url).data
        self.assertEqual(todo["count"], 5)
        self.assertIn("pages", todo)

        compras = self.client.get(url, {"direction": "outflow"}).data
        self.assertEqual(compras["count"], 1)

        buscado = self.client.get(url, {"search": "ANDINO"}).data
        self.assertEqual(buscado["count"], 1)

        confirmadas = self.client.get(url, {"status": "confirmed"}).data
        self.assertEqual(confirmadas["count"], 5)

    def test_rule_with_full_confidence_confirms(self):
        category = TransactionCategory.objects.get(taxpayer_id="", code="SALES_GROSS")
        categorization.create_rule_from(self.row, category)
        categorization.categorize(self.row)
        self.assertEqual(self.row.categorization_status, CategorizationStatus.CONFIRMED)

    def test_bulk_categorize_endpoint_learns_a_rule(self):
        response = self.client.post(
            reverse("financials:bulk-categorize"),
            {"ids": [str(self.row.id)], "category_code": "SALES_GROSS",
             "create_rule": True},
            format="json",
        )
        self.assertEqual(response.status_code, http.HTTP_200_OK)
        self.assertTrue(response.data["rule_created"])
        # The learned rule confirms the next invoice of the same RUC alone.
        make_invoice()
        ingest.ingest_sunat(RUC)
        categorization.categorize_pending(RUC)
        statuses = set(
            FinancialTransaction.objects.values_list(
                "categorization_status", flat=True
            )
        )
        self.assertEqual(statuses, {CategorizationStatus.CONFIRMED})


class IncomeStatementTests(TenantAPITestCase):
    """Hand-verified: sales 2000 − returns 200 − purchases 1000 admin."""

    def _confirm_all(self, code_by_direction):
        categorization.categorize_pending(RUC)
        for row in FinancialTransaction.objects.all():
            code = code_by_direction[
                (row.direction, row.is_credit_note)
            ]
            category = TransactionCategory.objects.get(taxpayer_id="", code=code)
            categorization.confirm(row, category, None)

    def test_lines_and_subtotals_square(self):
        make_invoice(total_amount=Decimal("1180.00"))
        make_invoice(total_amount=Decimal("1180.00"), period="202604",
                     issue_date=datetime.date(2026, 4, 10))
        make_invoice(
            document_class=DocumentClass.CREDIT_NOTE,
            total_amount=Decimal("236.00"),
        )
        make_invoice(
            direction=Direction.RECEIVED, issuer_ruc="20111111111",
            total_amount=Decimal("1180.00"),
        )
        ingest.ingest_sunat(RUC)
        self._confirm_all({
            ("inflow", False): "SALES_GROSS",
            ("inflow", True): "SALES_RETURNS",
            ("outflow", False): "ADMIN_EXPENSES",
        })

        result = statements.income_statement(RUC, 2026)
        lines = {l["code"]: l for l in result["lines"]}
        self.assertEqual(lines["GROSS_SALES"]["total"], 2000.0)
        self.assertEqual(lines["SALES_DEDUCTIONS"]["total"], -200.0)
        self.assertEqual(lines["NET_SALES"]["total"], 1800.0)
        self.assertEqual(lines["ADMIN_EXPENSES_LINE"]["total"], -1000.0)
        self.assertEqual(lines["OPERATING_PROFIT"]["total"], 800.0)
        self.assertEqual(lines["NET_INCOME"]["total"], 800.0)
        # Vertical analysis over net sales (§7.1).
        self.assertEqual(lines["NET_SALES"]["vertical_pct"], 100.0)
        self.assertEqual(lines["ADMIN_EXPENSES_LINE"]["vertical_pct"], -55.6)
        # March holds 1000 net of sales, April the other 1000.
        self.assertEqual(lines["GROSS_SALES"]["months"]["3"], 1000.0)
        self.assertEqual(lines["GROSS_SALES"]["months"]["4"], 1000.0)

    def test_drilldown_reaches_the_documents(self):
        make_invoice()
        ingest.ingest_sunat(RUC)
        row = FinancialTransaction.objects.get()
        category = TransactionCategory.objects.get(taxpayer_id="", code="SALES_GROSS")
        categorization.confirm(row, category, None)
        data = self.client.get(
            reverse("financials:drilldown"),
            {"line": "GROSS_SALES", "year": 2026, "month": 3},
        ).data
        self.assertEqual(len(data["transactions"]), 1)
        self.assertEqual(data["transactions"][0]["external_id"], row.external_id)
        self.assertEqual(
            data["transactions"][0]["source_object_id"], row.source_object_id
        )


class RatioTests(TenantAPITestCase):
    def test_zero_denominator_returns_null_never_infinity(self):
        rows = ratios.annual_ratios(RUC, 2026)
        current = next(r for r in rows if r["code"] == "CURRENT_RATIO")
        self.assertIsNone(current["value"])  # V11

    def test_partial_year_adjusts_base_days(self):
        make_invoice()
        ingest.ingest_sunat(RUC)
        row = FinancialTransaction.objects.get()
        category = TransactionCategory.objects.get(taxpayer_id="", code="SALES_GROSS")
        categorization.confirm(row, category, None)
        days, partial = ratios.base_days(RUC, 2026)
        self.assertTrue(partial)
        self.assertEqual(days, 30)  # one month with data × 30 (§8.4)


class MastersApiTests(TenantAPITestCase):
    def test_index_lists_payroll_and_financial_masters(self):
        data = self.client.get(reverse("financials:masters-index")).data
        keys = {row["key"] for row in data}
        self.assertIn("tax-unit", keys)
        self.assertIn("categories", keys)
        groups = {row["group"] for row in data}
        self.assertEqual(groups, {"Planilla", "Finanzas"})

    def test_crud_over_a_global_master(self):
        url = reverse("financials:master-list", args=["tax-unit"])
        listed = self.client.get(url).data
        self.assertEqual(listed["rows"][0]["year"], 2026)
        created = self.client.post(
            url, {"year": 2027, "amount": "5700.00"}, format="json"
        )
        self.assertEqual(created.status_code, http.HTTP_201_CREATED)
        detail = reverse(
            "financials:master-detail", args=["tax-unit", created.data["id"]]
        )
        patched = self.client.patch(detail, {"amount": "5750.00"}, format="json")
        self.assertEqual(patched.data["amount"], "5750.00")
        self.assertEqual(self.client.delete(detail).status_code, 204)

    def test_global_catalog_rows_cannot_be_edited(self):
        listed = self.client.get(
            reverse("financials:master-list", args=["categories"])
        ).data
        target = listed["rows"][0]["id"]
        response = self.client.patch(
            reverse("financials:master-detail", args=["categories", target]),
            {"name": "Otro nombre"}, format="json",
        )
        self.assertEqual(response.status_code, http.HTTP_409_CONFLICT)

    def test_company_singleton_appears_with_defaults(self):
        data = self.client.get(
            reverse("financials:master-list", args=["financial-settings"])
        ).data
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["functional_currency"], "PEN")


class KpisAcumuladoTests(TenantAPITestCase):
    """El acumulado del ejercicio y el peso de cada gasto sobre la venta."""

    def setUp(self):
        # Dos ventas de 1000 neto (marzo y abril) y una compra de 500 neto
        # (marzo), que la heurística confirma como costo de venta.
        make_invoice(issue_date=datetime.date(2026, 3, 10), period="202603")
        make_invoice(issue_date=datetime.date(2026, 4, 10), period="202604")
        make_invoice(
            direction=Direction.RECEIVED, issuer_ruc="20100070970",
            receiver_ruc=RUC, total_amount=Decimal("590.00"),
            issue_date=datetime.date(2026, 3, 12), period="202603",
        )
        ingest.ingest_sunat(RUC)
        categorization.categorize_pending(RUC)

    def test_acumulado_y_porcentajes(self):
        data = ratios.kpis(RUC, 2026)
        acumulado = data["accumulated"]
        self.assertEqual(acumulado["net_sales"], 2000.0)
        self.assertEqual(acumulado["cost_of_sales_line"], 500.0)
        self.assertEqual(acumulado["cost_of_sales_line_pct"], 25.0)
        self.assertEqual(acumulado["gross_profit"], 1500.0)
        self.assertEqual(acumulado["gross_profit_pct"], 75.0)
        self.assertEqual(acumulado["last_month_with_data"], 4)

        marzo = next(m for m in data["months"] if m["month"] == 3)
        self.assertEqual(marzo["cost_of_sales_line_pct"], 50.0)
        abril = next(m for m in data["months"] if m["month"] == 4)
        self.assertEqual(abril["cost_of_sales_line_pct"], 0.0)

    def test_el_historico_de_ratios_trae_un_bloque_por_anio(self):
        url = reverse("financials:ratios-history")
        response = self.client.get(url, {"years": 3, "year": 2026})
        self.assertEqual(response.status_code, http.HTTP_200_OK)
        anios = [b["year"] for b in response.data["years"]]
        self.assertEqual(anios, [2024, 2025, 2026])
        ultimo = response.data["years"][-1]["ratios"]
        margen = next(r for r in ultimo if r["code"] == "GROSS_MARGIN")
        self.assertEqual(margen["value"], 75.0)
        vacio = response.data["years"][0]["ratios"]
        self.assertTrue(all(r["value"] is None for r in vacio if r["code"] == "GROSS_MARGIN"))


class PeriodsTests(TenantAPITestCase):
    """Los filtros del tablero se arman con los periodos que existen."""

    def test_lista_solo_ejercicios_y_meses_con_datos(self):
        make_invoice(issue_date=datetime.date(2024, 11, 5), period="202411")
        make_invoice(issue_date=datetime.date(2026, 3, 10), period="202603")
        make_invoice(issue_date=datetime.date(2026, 4, 10), period="202604")
        ingest.ingest_sunat(RUC)

        response = self.client.get(reverse("financials:periods"))

        self.assertEqual(response.status_code, http.HTTP_200_OK)
        self.assertEqual(response.json()["years"], [
            {"year": 2026, "months": [4, 3]},
            {"year": 2024, "months": [11]},
        ])

    def test_sin_datos_devuelve_vacio(self):
        response = self.client.get(reverse("financials:periods"))
        self.assertEqual(response.json(), {"years": []})
