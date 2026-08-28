"""Lo declarado, visto desde los otros módulos: Inicio, calendario,
Colaboradores, Balance, obligaciones y asistente."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.urls import reverse

from core.testing import TenantAPITestCase
from obligations import enums
from obligations.services.context import CompanyContext
from obligations.services.evaluators import plame_declaration, tax_payments_current
from sunat_declaraciones.services import (
    cruce_balance, estado_del_mes, guardar, lineas_para_asistente, planilla_vs_plame,
    presentaciones_por_periodo,
)

AQUI = Path(__file__).parent
MUESTRA = json.loads((AQUI / "muestra.json").read_text(encoding="utf-8"))
CONSTANCIAS = json.loads((AQUI / "constancias.json").read_text(encoding="utf-8"))
RUC = "20100000009"


def ctx(hoy: date) -> CompanyContext:
    return CompanyContext(account_ruc=RUC, today=hoy, flat={})


class PanoramaTests(TenantAPITestCase):
    RUC = RUC

    def setUp(self):
        guardar(RUC, MUESTRA, constancias=CONSTANCIAS)

    def test_sin_datos_no_hay_estado(self):
        self.assertIsNone(estado_del_mes("20999999999"))

    def test_estado_del_mes_dice_que_toca_y_si_esta_presentado(self):
        e = estado_del_mes(RUC, hoy=date(2025, 2, 20))
        self.assertEqual(e["periodo"], "202501")
        self.assertTrue(e["igv_renta"]["presentado"])
        self.assertTrue(e["plame"]["presentado"])
        self.assertIsNone(e["vencimiento"])  # 2025 sin cronograma
        e = estado_del_mes(RUC, hoy=date(2025, 3, 20))
        self.assertFalse(e["igv_renta"]["presentado"])
        self.assertEqual(e["anterior"]["periodo"], "202501")
        self.assertTrue(e["anterior"]["igv_renta"]["presentado"])

    def test_historico_planilla_pone_las_fuentes_lado_a_lado(self):
        from afpnet.models import AfpnetPeriodSummary
        from sunat_declaraciones.services.panorama import historico_planilla

        AfpnetPeriodSummary.objects.create(taxpayer_id=RUC, period="202501", total_op=4)
        h = historico_planilla(RUC, hoy=date(2025, 2, 20))
        self.assertEqual(len(h["periodos"]), 12)
        enero = next(f for f in h["periodos"] if f["periodo"] == "202501")
        self.assertIsNotNone(enero["plame"]["trabajadores"])
        self.assertEqual(enero["afpnet"], 4)          # AFPnet no pisa a la PLAME
        self.assertIsNone(enero["planilla_propia"])    # sin planilla propia cerrada
        self.assertEqual(h["colaboradores_activos"], 0)
        self.assertEqual(h["ultima_plame"]["periodo"], "202501")
        self.assertEqual(h["ultimo_afpnet"], {"periodo": "202501", "aportantes": 4})
        r = self.client.get(reverse("sunat_declaraciones:planilla-historico"))
        self.assertEqual(r.status_code, 200)

    def test_api_panorama(self):
        r = self.client.get(reverse("sunat_declaraciones:panorama"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("estado", r.data)

    def test_presentaciones_por_periodo(self):
        p = presentaciones_por_periodo(RUC)
        self.assertEqual(p["202501"]["621"]["nro_orden"], "1120850831")
        self.assertEqual(p["202501"]["0601"]["nro_orden"], "1120849853")
        self.assertNotIn("0601", p["202503"])

    def test_planilla_vs_plame(self):
        from colaboradores.models import Colaborador

        Colaborador.objects.create(taxpayer_id=RUC, document_number="00000001", full_name="Ana Perez", is_active=True)
        d = planilla_vs_plame(RUC)
        self.assertEqual(d["colaboradores_activos"], 1)
        fila = d["periodos"][0]
        self.assertEqual(fila["periodo"], "202501")
        self.assertEqual(fila["trabajadores"], 6)
        self.assertEqual(fila["remuneraciones"], 15725.0)
        self.assertEqual(fila["costo_declarado"], 15725.0 + 1511.0)
        self.assertEqual(fila["diferencia_trabajadores"], 5)
        self.assertIsNone(fila["planilla_propia"])
        r = self.client.get(reverse("sunat_declaraciones:planilla"))
        self.assertEqual(r.status_code, 200)

    def test_lineas_para_asistente(self):
        lineas = lineas_para_asistente(RUC)
        self.assertTrue(any(l.startswith("[declaraciones]") for l in lineas))
        self.assertTrue(any(l.startswith("[621 202501]") and "24,141" in l for l in lineas))
        self.assertEqual(lineas_para_asistente("20999999999"), [])


class EvaluadoresTests(TenantAPITestCase):
    RUC = RUC

    def test_plame_sin_datos_es_sin_determinar(self):
        v = plame_declaration(ctx(date(2025, 2, 20)), None)
        self.assertEqual(v.compliance_status, enums.ComplianceStatus.UNKNOWN)

    def test_plame_presentada_es_verificada(self):
        guardar(RUC, MUESTRA, constancias=CONSTANCIAS)
        v = plame_declaration(ctx(date(2025, 2, 20)), None)
        self.assertEqual(v.compliance_status, enums.ComplianceStatus.COMPLIANT)
        self.assertEqual(v.verification_status, enums.VerificationStatus.VERIFIED)
        v = plame_declaration(ctx(date(2025, 3, 20)), None)  # 202502 sin PLAME y sin cronograma → no cumple
        self.assertEqual(v.compliance_status, enums.ComplianceStatus.NON_COMPLIANT)

    def test_pagos_al_dia_con_alerta_abierta(self):
        from finance_analytics.models import FinanceAlert, AlertSeverity

        guardar(RUC, MUESTRA, constancias=CONSTANCIAS)
        v = tax_payments_current(ctx(date(2025, 4, 30)), None)
        self.assertEqual(v.compliance_status, enums.ComplianceStatus.COMPLIANT)
        FinanceAlert.objects.create(account_ruc=RUC, dedup_key="decl:x", alert_type="declaracion_sin_pago", severity=AlertSeverity.HIGH, title="Falta pago", explanation="")
        v = tax_payments_current(ctx(date(2025, 4, 30)), None)
        self.assertEqual(v.compliance_status, enums.ComplianceStatus.NON_COMPLIANT)
        self.assertIn("Falta pago", v.reason)


class CalendarioTests(TenantAPITestCase):
    RUC = RUC

    def test_los_vencimientos_dicen_si_estan_presentados(self):
        from sensor_sunat.views_app import _con_presentaciones

        guardar(RUC, MUESTRA, constancias=CONSTANCIAS)
        eventos = _con_presentaciones(RUC, [
            {"tipo": "SUNAT_MENSUAL", "periodo": "202501", "fecha": date(2025, 2, 17), "titulo": "x"},
            {"tipo": "SUNAT_MENSUAL", "periodo": "202502", "fecha": date(2025, 3, 17), "titulo": "y"},
            {"tipo": "LABORAL", "fecha": date(2025, 5, 15), "titulo": "z"},
        ])
        self.assertEqual(eventos[0]["presentado"]["621"]["nro_orden"], "1120850831")
        self.assertFalse(eventos[0]["presentado"]["621"]["a_tiempo"])  # 18/02 > 17/02
        self.assertIsNone(eventos[1]["presentado"]["621"])
        self.assertNotIn("presentado", eventos[2])


class BalanceTests(TenantAPITestCase):
    RUC = RUC

    def test_cruce_balance_con_710(self):
        from sunat_declaraciones.services.renta_anual import PresentacionAnual, guardar as guardar_anual
        from sunat_declaraciones.models import DeclaracionAnual

        resumen = json.loads((AQUI / "renta_resumen.json").read_text(encoding="utf-8"))["presentacion"][0]
        detallado = json.loads((AQUI / "renta_detallado.json").read_text(encoding="utf-8"))
        guardar_anual(RUC, [PresentacionAnual(ejercicio="2025", formulario="0710", resumen=resumen, detallado=detallado)])
        try:
            data = cruce_balance(RUC, 2025)
            filas = {r["code"]: r for r in data["rows"]}
            self.assertEqual(filas["CASH_LINE"]["declarado"], 1770.0)
            self.assertEqual(filas["PPE_LINE"]["declarado"], 29660.0 - 7256.0)
            self.assertEqual(filas["TOTAL_ASSETS"]["declarado"], 409843.0)
            self.assertEqual(filas["PERIOD_RESULT"]["declarado"], -20515.0)
            r = self.client.get(reverse("sunat_declaraciones:renta-anual-cruce") + "?year=2025")
            self.assertIn("balance", r.data)
        finally:
            for d in DeclaracionAnual.objects.all():
                d.archivo.delete(save=False)
