"""El card «Deuda y pagos SUNAT»: boletas 1662 por mes y valores del buzón."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from django.urls import reverse

from core.testing import TenantAPITestCase
from sunat_declaraciones.models import DeclaracionPresentada
from sunat_declaraciones.services.deuda import resumen_deuda
from sunat_mailbox.models import Message

RUC = "20100000009"
HOY = date(2026, 8, 28)


def boleta(fecha: date, importe: str, periodo: str = "202604", formulario: str = "1662",
           tributos: list[tuple[str, str, str, str]] | None = None):
    """``tributos`` = [(codTri, descripcion, importe, perTri)] de la constancia."""
    constancia = {"tributos": [
        {"codTri": c, "descCodTri": d, "mtoPagtot": float(i), "perTri": int(pt)} for c, d, i, pt in (tributos or [])
    ]} if tributos else {}
    return DeclaracionPresentada.objects.create(
        account_ruc=RUC, periodo=periodo, formulario=formulario, nro_orden=f"{fecha:%Y%m%d}{importe}",
        fecha_presentacion=fecha, importe_pagado=Decimal(importe), visto_el=HOY, constancia=constancia,
    )


def aviso(asunto: str, dia: date):
    return Message.objects.create(
        taxpayer_id=RUC, message_code=f"{dia:%Y%m%d}{abs(hash(asunto)) % 10000}",
        message_type=1, subject=asunto,
        published_at=datetime(dia.year, dia.month, dia.day, tzinfo=timezone.utc),
    )


class DeudaTests(TenantAPITestCase):
    RUC = RUC

    def test_boletas_por_mes_de_pago_y_solo_1662(self):
        boleta(date(2026, 8, 18), "4870.00")
        boleta(date(2026, 8, 26), "1997.00")
        boleta(date(2026, 3, 2), "500.00")
        boleta(date(2025, 6, 1), "9999.00")                      # fuera de los 12 meses
        boleta(date(2026, 8, 5), "3000.00", formulario="0621")   # declaración, no pago de deuda

        r = resumen_deuda(RUC, hoy=HOY)
        self.assertEqual(r["pagos"]["total_12m"], 7367.0)
        self.assertEqual(r["pagos"]["boletas_12m"], 3)
        serie = {p["periodo"]: p["importe"] for p in r["pagos"]["serie"]}
        self.assertEqual(len(serie), 12)
        self.assertEqual(serie["202608"], 6867.0)
        self.assertEqual(serie["202603"], 500.0)
        self.assertEqual(serie["202601"], 0.0)
        self.assertEqual(r["pagos"]["ultimo"]["importe"], 1997.0)
        self.assertEqual(r["pagos"]["ultimo"]["fecha"], date(2026, 8, 26))

    def test_multas_y_desglose_por_clase(self):
        boleta(date(2026, 8, 18), "4870.00", tributos=[
            ("1011", "IGV - OPER. INT.", "4000.00", "202604"),
            ("6411", "RETENC.O PERCEPC.NO PAGADAS", "870.00", "202604"),
        ])
        boleta(date(2026, 7, 31), "733.00", periodo="202603", tributos=[
            ("6111", "RETENC.NO PAG.EN PZOS.", "733.00", "202603"),
        ])
        boleta(date(2026, 6, 1), "100.00")  # sin constancia: cuenta en la serie, no en clases

        pagos = resumen_deuda(RUC, hoy=HOY)["pagos"]
        self.assertEqual(pagos["multas"]["total_12m"], 1603.0)
        self.assertEqual(pagos["multas"]["pagos_12m"], 2)
        primera = pagos["multas"]["detalle"][0]
        self.assertEqual(primera["fecha"], date(2026, 8, 18))
        self.assertEqual(primera["codigo"], "6411")
        self.assertEqual(primera["periodo"], "202604")
        agosto = next(m for m in pagos["serie"] if m["periodo"] == "202608")
        self.assertEqual(agosto["boletas"], 1)
        self.assertEqual([(c["clase"], c["importe"]) for c in agosto["clases"]], [("igv", 4000.0), ("multa", 870.0)])
        clases = {c["clase"]: c for c in pagos["por_clase"]}
        self.assertEqual(clases["igv"]["importe"], 4000.0)
        self.assertEqual(clases["multa"]["importe"], 1603.0)
        self.assertEqual(clases["multa"]["label"], "Multa / intereses")

    def test_valores_del_buzon(self):
        aviso("ASUNTO: Notificación de Orden de Pago N° 030-001-1 en Buzón Electrónico SOL", date(2026, 4, 27))
        aviso("ASUNTO: Notificación de Resolución de Multa N° 023-002-2 en Buzón Electrónico SOL", date(2026, 7, 22))
        aviso("ASUNTO: Notificación de Resolución de Ejecución Coactiva N° 029-006-3 en Buzón", date(2026, 4, 30))
        aviso("ASUNTO: Notificación de Resolución Coactiva de Conclusión con Numeral de Sustento N° 4", date(2026, 5, 21))
        aviso("Aviso informativo: campaña de renta", date(2026, 3, 1))
        aviso("ASUNTO: Notificación de Orden de Pago N° 030-001-9 antigua", date(2025, 1, 1))

        v = resumen_deuda(RUC, hoy=HOY)["valores"]
        self.assertEqual(v["notificados_12m"], 2)
        self.assertEqual(v["multas_12m"], 1)
        self.assertEqual(v["coactiva_12m"], 1)
        self.assertEqual(v["concluidos_12m"], 1)
        self.assertEqual(v["ultimo"]["fecha"], date(2026, 7, 22))
        self.assertTrue(v["ultimo"]["asunto"].startswith("Notificación de Resolución de Multa"))
        self.assertFalse(v["ultimo"]["en_coactiva"])

    def test_api(self):
        r = self.client.get(reverse("sunat_declaraciones:deuda"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["pagos"]["boletas_12m"], 0)
        self.assertIsNone(r.data["ficha"]["coactiva_publicada"])
