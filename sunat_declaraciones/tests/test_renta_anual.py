"""La DJ anual desde e-renta: casillas con nombre, zip con anexos, evidencia."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.urls import reverse

from core.testing import TenantAPITestCase
from obligations.models import CompanyObligation, ComplianceRule, ObligationEvidence
from sunat_declaraciones.models import DeclaracionAnual
from sunat_declaraciones.services.renta_anual import (
    PresentacionAnual, anexos_de, casillas_de, con_nombre, resumen_anual, sincronizar_renta_anual,
)

AQUI = Path(__file__).parent
RESUMEN = json.loads((AQUI / "renta_resumen.json").read_text(encoding="utf-8"))
DETALLADO = json.loads((AQUI / "renta_detallado.json").read_text(encoding="utf-8"))
ZIP = (AQUI / "renta_anual.zip").read_bytes()
RUC = "20100000009"


def presentacion(zip_bytes=ZIP, detallado=DETALLADO):
    return PresentacionAnual(ejercicio="2025", formulario="0710", resumen=RESUMEN["presentacion"][0], detallado=detallado, zip_bytes=zip_bytes)


class ParseoTests(TenantAPITestCase):
    RUC = RUC

    def test_casillas_con_nombre(self):
        c = con_nombre(casillas_de(DETALLADO))
        self.assertEqual(c["ventas_netas"], Decimal("387310"))
        self.assertEqual(c["costo_de_ventas"], Decimal("255344"))
        self.assertEqual(c["perdida_neta"], Decimal("20515"))
        self.assertEqual(c["total_activo"], Decimal("409843"))
        self.assertEqual(c["pagos_a_cuenta"], Decimal("3884"))
        self.assertEqual(c["saldo_a_favor"], Decimal("3884"))
        self.assertEqual(c["impuesto_a_la_renta"], Decimal("0"))
        self.assertIsNone(c["gastos_de_ventas"])  # casilla ausente = None, no 0

    def test_anexos_del_zip(self):
        anexos = anexos_de(ZIP)
        self.assertEqual(list(anexos), ["PrincipalesSocios"])
        self.assertEqual(anexos["PrincipalesSocios"][1][2], "PEREZ ANA")
        self.assertEqual(anexos_de(b"no es zip"), {})


class SincronizarTests(TenantAPITestCase):
    RUC = RUC

    def _sync(self, presentaciones):
        with patch("sunat_declaraciones.services.renta_anual.RentaAnualClient") as cliente:
            cliente.return_value.consultar.return_value = presentaciones
            return sincronizar_renta_anual(RUC, "USUARIO", "clave"), cliente

    def test_guarda_casillas_zip_y_anexos(self):
        r, _ = self._sync([presentacion()])
        self.assertEqual((r.presentaciones, r.nuevas), (1, 1))
        d = DeclaracionAnual.objects.get(account_ruc=RUC, ejercicio="2025")
        self.assertEqual(d.nro_orden, "1160136986")
        self.assertEqual(d.casillas["461"], "387310")
        self.assertEqual(d.tributos[0]["codigo"], "030801")
        self.assertTrue(d.archivo.name.endswith(".zip"))
        self.assertIn("PrincipalesSocios", d.anexos)
        self.assertEqual(d.fecha_presentacion.date(), date(2026, 3, 20))
        d.archivo.delete(save=False)

    def test_lo_ya_traido_se_omite_y_no_se_pisa(self):
        self._sync([presentacion()])
        r, cliente = self._sync([presentacion(zip_bytes=None, detallado=None)])
        self.assertIn("1160136986", cliente.return_value.consultar.call_args.kwargs["omitir"])
        d = DeclaracionAnual.objects.get(account_ruc=RUC, ejercicio="2025")
        self.assertEqual(d.casillas["461"], "387310")
        self.assertTrue(d.archivo)
        d.archivo.delete(save=False)

    def test_es_evidencia_verificada_de_la_obligacion_anual(self):
        ob = CompanyObligation.objects.create(account_ruc=RUC, rule=ComplianceRule.objects.get(code="tax-annual-income"))
        self._sync([presentacion()]); self._sync([presentacion()])
        ev = ObligationEvidence.objects.filter(company_obligation=ob)
        self.assertEqual(ev.count(), 1)
        self.assertEqual(ev.get().verification_status, "verified")
        self.assertEqual(ev.get().valid_until, date(2027, 4, 30))
        DeclaracionAnual.objects.get(account_ruc=RUC).archivo.delete(save=False)


class LecturaTests(TenantAPITestCase):
    RUC = RUC

    def setUp(self):
        with patch("sunat_declaraciones.services.renta_anual.RentaAnualClient") as cliente:
            cliente.return_value.consultar.return_value = [presentacion()]
            sincronizar_renta_anual(RUC, "USUARIO", "clave")

    def tearDown(self):
        for d in DeclaracionAnual.objects.all():
            d.archivo.delete(save=False)

    def test_depreciacion_y_ajuste_del_impuesto_van_al_estado_de_resultados(self):
        from financials.models import FinancialTransaction, TransactionSource

        filas = FinancialTransaction.objects.filter(taxpayer_id=RUC, source=TransactionSource.SUNAT_ANNUAL)
        dep = filas.filter(external_id__startswith="710-2025-dep-")
        self.assertEqual(dep.count(), 12)
        # sin 710 anterior, la acumulada entera (7,256) en doceavos
        self.assertEqual(dep.first().net_amount_pen, Decimal("604.67"))
        self.assertEqual(dep.first().category.code, "DEPRECIATION")
        # impuesto 0 y sin pagos a cuenta cargados → no hay ajuste
        self.assertFalse(filas.filter(external_id="710-2025-tax").exists())

    def test_el_ajuste_devuelve_los_pagos_a_cuenta_cuando_hay_perdida(self):
        from financials.models import FinancialTransaction, TransactionSource
        from financials.services.ingest import _category, _sunat_fields, _upsert, ingest_annual
        from datetime import date as _d

        _upsert(RUC, TransactionSource.SUNAT_DECLARATION, "621-202501", _sunat_fields("pac", _d(2025, 1, 1), Decimal("281"), _category(RUC, "INCOME_TAX")))
        ingest_annual(RUC)
        ajuste = FinancialTransaction.objects.get(taxpayer_id=RUC, source=TransactionSource.SUNAT_ANNUAL, external_id="710-2025-tax")
        self.assertEqual(ajuste.net_amount_pen, Decimal("-281.00"))  # impuesto 0 − pagos 281
        self.assertEqual(ajuste.accounting_date, _d(2025, 12, 1))

    def test_resumen_anual(self):
        e = resumen_anual(RUC)[0]
        self.assertEqual(e["ejercicio"], "2025")
        self.assertEqual(e["resultados"]["ventas_netas"], 387310.0)
        self.assertEqual(e["impuesto"]["saldo_a_favor"], 3884.0)
        self.assertEqual(e["anexos"], {"PrincipalesSocios": 1})
        self.assertTrue(e["tiene_archivo"])

    def test_api(self):
        r = self.client.get(reverse("sunat_declaraciones:renta-anual"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["ejercicios"][0]["nro_orden"], "1160136986")

    def test_la_renta_del_ejercicio_trae_la_dj_declarada(self):
        from finance_analytics.services.renta import renta_summary

        data = renta_summary(RUC, "RMT", 2025, [])
        self.assertEqual(data["dj_anual"]["impuesto"]["pagos_a_cuenta"], 3884.0)
        self.assertIsNone(renta_summary(RUC, "RMT", 2024, [])["dj_anual"])


class CruceTests(TenantAPITestCase):
    RUC = RUC

    def setUp(self):
        with patch("sunat_declaraciones.services.renta_anual.RentaAnualClient") as cliente:
            cliente.return_value.consultar.return_value = [presentacion()]
            sincronizar_renta_anual(RUC, "USUARIO", "clave")

    def tearDown(self):
        for d in DeclaracionAnual.objects.all():
            d.archivo.delete(save=False)

    def test_cruza_con_los_signos_de_finanzas(self):
        from sunat_declaraciones.services import cruce_estado_resultados

        data = cruce_estado_resultados(RUC, 2025)
        filas = {r["code"]: r for r in data["rows"]}
        self.assertEqual(data["declaracion"]["nro_orden"], "1160136986")
        self.assertEqual(filas["NET_SALES"]["declarado"], 387310.0)
        self.assertEqual(filas["COST_OF_SALES_LINE"]["declarado"], -255344.0)
        self.assertEqual(filas["NET_INCOME"]["declarado"], -20515.0)
        self.assertEqual(filas["OPERATING_PROFIT"]["declarado"], -14458.0)
        self.assertIsNone(filas["SELLING_EXPENSES_LINE"]["declarado"])  # casilla ausente
        # Sin comprobantes en Finanzas: la diferencia es todo lo declarado.
        self.assertEqual(filas["NET_SALES"]["finanzas"], 0.0)
        self.assertEqual(filas["NET_SALES"]["diferencia"], 387310.0)
        self.assertIsNone(filas["NET_SALES"]["diferencia_pct"])

    def test_sin_dj_no_hay_declarado(self):
        from sunat_declaraciones.services import cruce_estado_resultados

        data = cruce_estado_resultados(RUC, 2024)
        self.assertIsNone(data["declaracion"])
        self.assertTrue(all(r["declarado"] is None for r in data["rows"]))

    def test_api_cruce(self):
        r = self.client.get(reverse("sunat_declaraciones:renta-anual-cruce") + "?year=2025")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["year"], 2025)
        self.assertEqual(self.client.get(reverse("sunat_declaraciones:renta-anual-cruce") + "?year=25").status_code, 400)
