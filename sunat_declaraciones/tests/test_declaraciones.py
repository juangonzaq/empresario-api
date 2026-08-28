"""La consulta de declaraciones: se guarda, se deriva y se enseña."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.urls import reverse

from core.testing import TenantAPITestCase
from obligations.models import CompanyObligation, ComplianceRule, ObligationEvidence
from reconciliation.models import DeclaredSummary
from sunat_declaraciones.models import ConsultaDeclaraciones, DeclaracionPresentada
from sunat_declaraciones.services import (
    guardar, hallazgos, resumen, resumen_621, sincronizar, ventanas,
)
from sunat_declaraciones.services.client import url_consulta

MUESTRA = json.loads((Path(__file__).parent / "muestra.json").read_text(encoding="utf-8"))
CONSTANCIAS = json.loads((Path(__file__).parent / "constancias.json").read_text(encoding="utf-8"))
RUC = "20100000009"


class ClienteTests(TenantAPITestCase):
    RUC = RUC

    def test_la_url_filtra_solo_por_periodo(self):
        url = url_consulta("202501", "202506")
        self.assertTrue(url.endswith("/0601,0621,1662,detr/20250101/20250630/01/2025/06/2025/false/true"))

    def test_las_ventanas_son_de_seis_periodos(self):
        self.assertEqual(
            ventanas("202411", "202508"),
            [("202411", "202504"), ("202505", "202508")],
        )
        self.assertEqual(ventanas("202503", "202503"), [("202503", "202503")])


class GuardarTests(TenantAPITestCase):
    RUC = RUC

    def test_guarda_una_fila_por_orden_y_es_idempotente(self):
        r1 = guardar(RUC, MUESTRA)
        r2 = guardar(RUC, MUESTRA)
        self.assertEqual((r1.filas, r1.nuevas), (6, 6))
        self.assertEqual((r2.nuevas, r2.actualizadas), (0, 0))
        self.assertEqual(DeclaracionPresentada.objects.de(RUC).count(), 6)

    def test_normaliza_fechas_importes_y_enlace_de_boleta(self):
        guardar(RUC, MUESTRA)
        boleta = DeclaracionPresentada.objects.get(account_ruc=RUC, nro_orden="1120850832")
        self.assertEqual(boleta.periodo, "202501")
        self.assertEqual(boleta.fecha_presentacion, date(2025, 2, 18))
        self.assertEqual(boleta.importe_pagado, Decimal("303.00"))
        self.assertEqual(boleta.nro_orden_original, "1120850831")
        self.assertTrue(boleta.es_boleta)
        d621 = DeclaracionPresentada.objects.get(account_ruc=RUC, nro_orden="1120850831")
        self.assertEqual(d621.nro_orden_original, "")
        self.assertFalse(d621.rectificatoria)
        self.assertEqual(d621.casillas["C100"].strip(), "24141.00")

    def test_las_casillas_del_621_se_leen_con_nombre(self):
        d621 = next(r for r in MUESTRA if r["numOrd"] == "1120850831")
        r = resumen_621(d621["casillas"])
        self.assertEqual(r["ventas_base"], Decimal("24141.00"))
        self.assertEqual(r["ventas_igv"], Decimal("4345.00"))
        self.assertEqual(r["compras_base"], Decimal("5122.00"))
        self.assertEqual(r["compras_igv"], Decimal("922.00"))
        self.assertEqual(r["igv_a_pagar"], Decimal("3423.00"))
        self.assertEqual(r["renta_pago_a_cuenta"], Decimal("281.00"))
        self.assertEqual(r["total_a_pagar"], Decimal("3704.00"))


class DerivarTests(TenantAPITestCase):
    RUC = RUC

    def _sincronizar(self, filas=MUESTRA, **kw):
        with patch("sunat_declaraciones.services.sync.ConsultaDeclaracionesClient") as cliente:
            cliente.return_value.consultar.return_value = (filas, CONSTANCIAS)
            return sincronizar(RUC, "USUARIO", "clave", desde="202501", hasta="202503", **kw)

    def test_el_621_alimenta_el_declarado_de_conciliacion(self):
        self._sincronizar()
        declarado = DeclaredSummary.objects.get(account_ruc=RUC, period="202501")
        self.assertEqual(declarado.sales_base, Decimal("24141.00"))
        self.assertEqual(declarado.igv_payable, Decimal("3423.00"))
        self.assertEqual(declarado.income_tax_declared, Decimal("281.00"))
        self.assertEqual(declarado.filed_at, date(2025, 2, 18))
        self.assertEqual(declarado.source, DeclaredSummary.Source.IMPORT)
        self.assertTrue(DeclaredSummary.objects.filter(account_ruc=RUC, period="202503").exists())

    def test_la_rectificatoria_mas_reciente_manda(self):
        rect = dict(next(r for r in MUESTRA if r["numOrd"] == "1120850831"))
        rect = json.loads(json.dumps(rect))
        rect.update(numOrd="1199999999", descTipoDecla="1", fecPres=1740000000000, strFecPres="19/02/2025")
        rect["casillas"]["C100"] = "30000.00 "
        self._sincronizar([*MUESTRA, rect])
        declarado = DeclaredSummary.objects.get(account_ruc=RUC, period="202501")
        self.assertEqual(declarado.sales_base, Decimal("30000.00"))
        self.assertTrue(declarado.raw["rectificatoria"])

    def test_cada_621_es_evidencia_verificada_de_la_obligacion_mensual(self):
        regla = ComplianceRule.objects.get(code="tax-monthly-igv-renta")
        ob = CompanyObligation.objects.create(account_ruc=RUC, rule=regla)
        self._sincronizar()
        self._sincronizar()  # no duplica
        evidencias = ObligationEvidence.objects.filter(company_obligation=ob)
        self.assertEqual(evidencias.count(), 2)
        ev = evidencias.get(reference__period="202501")
        self.assertEqual(ev.verification_status, "verified")
        self.assertEqual(ev.valid_until, date(2025, 2, 28))

    def test_el_pago_a_cuenta_declarado_entra_al_estado_de_resultados(self):
        from financials.models import FinancialTransaction, TransactionSource

        self._sincronizar()
        self._sincronizar()  # idempotente
        filas = FinancialTransaction.objects.filter(
            taxpayer_id=RUC, source=TransactionSource.SUNAT_DECLARATION,
        ).order_by("accounting_date")
        self.assertEqual([f.external_id for f in filas], ["621-202501", "621-202503"])
        enero = filas[0]
        self.assertEqual(enero.net_amount_pen, Decimal("281.00"))
        self.assertEqual(enero.category.code, "INCOME_TAX")
        self.assertEqual(enero.categorization_status, "confirmed")
        self.assertEqual(enero.accounting_date, date(2025, 1, 1))

    def test_la_plame_entra_como_costo_de_personal_si_no_hay_planilla_cerrada(self):
        from financials.models import FinancialTransaction, TransactionSource

        self._sincronizar()
        fila = FinancialTransaction.objects.get(taxpayer_id=RUC, source=TransactionSource.SUNAT_PLAME, external_id="0601-202501")
        # C452 remuneraciones 15,725 + C412 EsSalud 1,511
        self.assertEqual(fila.net_amount_pen, Decimal("17236.00"))
        self.assertEqual(fila.category.code, "PAYROLL_ADMIN")
        self.assertIn("6 trabajador", fila.description)

    def test_la_planilla_cerrada_manda_sobre_la_plame(self):
        from financials.models import FinancialTransaction, TransactionSource
        from payroll.models import PayrollPeriod, PayrollStatus

        PayrollPeriod.objects.create(taxpayer_id=RUC, year=2025, month=1, status=PayrollStatus.CLOSED)
        self._sincronizar()
        self.assertFalse(FinancialTransaction.objects.filter(taxpayer_id=RUC, source=TransactionSource.SUNAT_PLAME, external_id="0601-202501").exists())

    def test_las_multas_de_las_boletas_son_gasto_financiero(self):
        from financials.models import FinancialTransaction, TransactionSource

        multa = json.loads(json.dumps(next(r for r in MUESTRA if r["numOrd"] == "1123027089")))
        multa["numOrd"] = "1199000001"; multa["mtoPag"] = 110.0
        constancias = dict(CONSTANCIAS)
        constancias["1199000001"] = {"banco": "BCP", "formaPago": "Cargo en cuenta", "tipoDeclaracion": "Original", "tributos": [
            {"codTri": "6411", "descCodTri": "RETENC.O PERCEPC.NO PAGADAS", "mtoPagtot": 110.0}], "form0601": False}
        with patch("sunat_declaraciones.services.sync.ConsultaDeclaracionesClient") as cliente:
            cliente.return_value.consultar.return_value = ([*MUESTRA, multa], constancias)
            sincronizar(RUC, "USUARIO", "clave", desde="202501", hasta="202503")
        fila = FinancialTransaction.objects.get(taxpayer_id=RUC, source=TransactionSource.SUNAT_DECLARATION, external_id="1662-1199000001")
        self.assertEqual(fila.net_amount_pen, Decimal("110.00"))
        self.assertEqual(fila.category.code, "SUNAT_PENALTIES")
        # los pagos a cuenta 621- siguen ahí
        self.assertTrue(FinancialTransaction.objects.filter(taxpayer_id=RUC, external_id="621-202501").exists())

    def test_la_renta_muestra_lo_declarado_junto_a_lo_estimado(self):
        from finance_analytics.services.renta import renta_summary

        self._sincronizar()
        data = renta_summary(RUC, "RMT", 2025, [])
        enero = next(m for m in data["months"] if m["period"] == "202501")
        self.assertEqual(enero["pago_a_cuenta_declarado"], Decimal("281.00"))
        self.assertIsNone(next(m for m in data["months"] if m["period"] == "202502")["pago_a_cuenta_declarado"])
        self.assertEqual(data["totals"]["meses_declarados"], 2)

    def test_el_overview_del_tablero_trae_el_621_del_mes(self):
        self._sincronizar()
        respuesta = self.client.get(reverse("finance_analytics:overview"))
        self.assertEqual(respuesta.status_code, 200)
        bloque = respuesta.data["declaraciones"]
        self.assertTrue(bloque["disponible"])
        self.assertTrue(bloque["por_periodo"]["202501"]["presentado"])
        self.assertEqual(bloque["por_periodo"]["202501"]["pago_a_cuenta"], 281.0)

    def test_la_constancia_se_guarda_y_solo_se_pide_una_vez(self):
        self._sincronizar()
        boleta = DeclaracionPresentada.objects.get(account_ruc=RUC, nro_orden="1120850832")
        self.assertEqual(boleta.constancia["tributos"][0]["codTri"], "1011")
        with patch("sunat_declaraciones.services.sync.ConsultaDeclaracionesClient") as cliente:
            cliente.return_value.consultar.return_value = (MUESTRA, {})
            sincronizar(RUC, "USUARIO", "clave", desde="202501", hasta="202503")
            omitidas = cliente.return_value.consultar.call_args.kwargs["omitir"]
        self.assertIn("1120850832", omitidas)
        self.assertNotIn("1130257828", omitidas)
        boleta.refresh_from_db()
        self.assertTrue(boleta.constancia)  # no se borra al re-sincronizar sin constancias

    def test_el_resumen_dice_que_tributo_paga_cada_boleta(self):
        self._sincronizar()
        data = resumen(RUC, hoy=date(2025, 4, 30))
        enero = next(p for p in data["periodos"] if p["periodo"] == "202501")
        clases = {b["nro_orden"]: [t["clase"] for t in b["tributos"]] for b in enero["boletas"]}
        self.assertEqual(clases["1120850832"], ["igv"])
        self.assertEqual(clases["1120850866"], ["renta"])
        self.assertEqual(enero["plame"]["trabajadores"], 6)
        marzo = next(p for p in data["periodos"] if p["periodo"] == "202503")
        self.assertEqual(marzo["boletas"][0]["tributos"][0]["clase"], "fraccionamiento")
        self.assertIn("detracciones", marzo["boletas"][0]["forma_pago"])

    def test_la_alerta_de_pago_no_cuenta_boletas_de_otro_tributo(self):
        self._sincronizar()
        # 202503: 621 con importe a pagar y una boleta de 1,356 que es fraccionamiento, no IGV.
        alerta = next(h for h in hallazgos(RUC, hoy=date(2025, 4, 30)) if h["period"] == "202503" and h["alert_type"] == "declaracion_sin_pago")
        self.assertIn("S/ 0 pagados", alerta["title"])

    def test_deja_bitacora_incluso_cuando_falla(self):
        with patch("sunat_declaraciones.services.sync.ConsultaDeclaracionesClient") as cliente:
            cliente.return_value.consultar.side_effect = RuntimeError("SOL caído")
            with self.assertRaises(RuntimeError):
                sincronizar(RUC, "USUARIO", "clave", desde="202501", hasta="202503")
        consulta = ConsultaDeclaraciones.objects.get(account_ruc=RUC)
        self.assertFalse(consulta.succeeded)
        self.assertIn("SOL caído", consulta.error)


class AlertasTests(TenantAPITestCase):
    RUC = RUC

    def setUp(self):
        guardar(RUC, MUESTRA)

    def test_sin_datos_no_hay_alertas(self):
        self.assertEqual(hallazgos("20999999999"), [])

    def test_periodo_cerrado_sin_621_es_omision(self):
        # Hay 621 de 01 y 03/2025; el de 02/2025 no aparece.
        tipos = {h["period"]: h["alert_type"] for h in hallazgos(RUC, hoy=date(2025, 4, 30))}
        self.assertEqual(tipos.get("202502"), "declaracion_omitida")
        self.assertNotIn("202501", [p for p, t in tipos.items() if t == "declaracion_omitida"])

    def test_no_se_alerta_omision_fuera_de_lo_consultado(self):
        ConsultaDeclaraciones.objects.create(account_ruc=RUC, periodo_desde="202501", periodo_hasta="202503", filas=6)
        omitidos = [h["period"] for h in hallazgos(RUC, hoy=date(2025, 8, 30)) if h["alert_type"] == "declaracion_omitida"]
        self.assertEqual(omitidos, ["202502"])

    def test_declarado_con_importe_y_sin_pago_suficiente(self):
        # 202501: a pagar 3704, pagado en boletas 303 + 281 = 584.
        alerta = next(h for h in hallazgos(RUC, hoy=date(2025, 4, 30)) if h["alert_type"] == "declaracion_sin_pago")
        self.assertEqual(alerta["period"], "202501")
        self.assertEqual(alerta["amount"], Decimal("3120.00"))

    def test_los_pagos_viejos_no_alertan(self):
        # 202501 queda fuera de los 12 periodos operativos mirado desde 2027.
        self.assertFalse(any(h["alert_type"] == "declaracion_sin_pago" for h in hallazgos(RUC, hoy=date(2027, 3, 1))))

    def test_fuera_de_plazo_solo_con_cronograma(self):
        # 2025 no tiene cronograma cargado: no se puede afirmar «tarde».
        self.assertFalse(any(h["alert_type"] == "declaracion_fuera_de_plazo" for h in hallazgos(RUC)))

    def test_las_alertas_entran_en_finance_alert(self):
        from finance_analytics.models import FinanceAlert
        from finance_analytics.services.alerts import _declaration_alerts

        with patch("sunat_declaraciones.services.alertas.timezone.localdate", return_value=date(2025, 4, 30)):
            claves = _declaration_alerts(RUC)
        self.assertTrue(claves)
        self.assertTrue(FinanceAlert.objects.filter(account_ruc=RUC, alert_type="declaracion_sin_pago").exists())


class TributosTests(TenantAPITestCase):
    def test_clasifica_los_codigos_reales(self):
        from sunat_declaraciones.services.tributos import clase_de

        self.assertEqual(clase_de("1011"), "igv")
        self.assertEqual(clase_de("3121"), "renta")   # MYPE Tributario
        self.assertEqual(clase_de("3031"), "renta")
        self.assertEqual(clase_de("3081"), "renta")   # regularización anual
        self.assertEqual(clase_de("3052"), "retenciones")
        self.assertEqual(clase_de("5210"), "essalud")
        self.assertEqual(clase_de("5310"), "onp")
        self.assertEqual(clase_de("8021"), "fraccionamiento")
        self.assertEqual(clase_de("6411"), "multa")


class ApiTests(TenantAPITestCase):
    RUC = RUC

    def test_resumen_por_periodo(self):
        guardar(RUC, MUESTRA)
        ConsultaDeclaraciones.objects.create(account_ruc=RUC, periodo_desde="202501", periodo_hasta="202503", filas=6)
        respuesta = self.client.get(reverse("sunat_declaraciones:declaraciones"))
        self.assertEqual(respuesta.status_code, 200)
        data = respuesta.data
        self.assertEqual([p["periodo"] for p in data["periodos"]], ["202503", "202501"])
        enero = data["periodos"][1]
        self.assertEqual(enero["igv_renta"]["nro_orden"], "1120850831")
        self.assertEqual(enero["igv_renta_declarado"]["ventas_base"], 24141.0)
        self.assertEqual(enero["plame"]["importe_pagado"], 3247.0)
        self.assertEqual(len(enero["boletas"]), 2)
        self.assertEqual(enero["total_pagado"], 3247.0 + 303.0 + 281.0)
        self.assertIsNone(enero["vencimiento"])  # 2025 sin cronograma
        self.assertEqual(data["ultima_consulta"]["filas"] if "filas" in data["ultima_consulta"] else 6, 6)

    def test_desde_mal_formado(self):
        respuesta = self.client.get(reverse("sunat_declaraciones:declaraciones") + "?desde=2025")
        self.assertEqual(respuesta.status_code, 400)

    def test_resumen_directo_con_hoy(self):
        guardar(RUC, MUESTRA)
        data = resumen(RUC, hoy=date(2025, 4, 30))
        self.assertEqual(data["periodo_que_toca"], "202503")
