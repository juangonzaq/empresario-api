"""El registro de proveedores visto como lo ve quien dirige la empresa.

Tres preguntas, y cada una es un bloque de este archivo:

* ¿me estoy metiendo en un lío al dar de alta a este proveedor? — el alta
  consulta SUNAT antes de guardar y se planta si el RUC está marcado.
* ¿a quién le compro de verdad? — la cartera se propone desde los
  comprobantes recibidos, no desde la memoria de nadie.
* ¿cuánto me puede costar? — el IGV de las facturas de proveedores marcados,
  que es lo que se discute en una fiscalización.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.urls import reverse
from rest_framework import status as http

from core.testing import DEFAULT_RUC, TenantAPITestCase
from suppliers.models import Supplier, SupplierCheck
from suppliers.services import RucLookupError
from sunat_cpe.models import Direction, DocumentClass, ElectronicInvoice

from .factories import RUC_ACTIVE, create_supplier
from .test_monitor import profile

# RUC válidos y distintos del de la empresa (``DEFAULT_RUC``), que el
# descubrimiento excluye a propósito: uno no es proveedor de sí mismo.
PROVEEDOR_NUEVO = "20131312955"
PROVEEDOR_CARO = "20614981556"


def comprobante(issuer_ruc: str, total: str, **overrides) -> ElectronicInvoice:
    datos = {
        "account_ruc": DEFAULT_RUC,
        "direction": Direction.RECEIVED,
        "document_class": DocumentClass.INVOICE,
        "document_type": "01",
        "issuer_ruc": issuer_ruc,
        "issuer_name": f"PROVEEDOR {issuer_ruc}",
        "series": "F001",
        "number": str(ElectronicInvoice.objects.count() + 1),
        "period": "202607",
        "currency": "PEN",
        "issue_date": date(2026, 7, 15),
        "total_amount": Decimal(total),
    }
    datos.update(overrides)
    datos["full_number"] = f"{datos['series']}-{datos['number']}"
    return ElectronicInvoice.objects.create(**datos)


class AltaVerificadaTests(TenantAPITestCase):
    """Registrar a ciegas es lo que sale caro meses después."""

    def setUp(self):
        self.url = reverse("suppliers:supplier-list")

    def alta(self, perfil=None, error=None, **datos):
        with patch("suppliers.views.RucLookupClient") as cliente:
            if error is not None:
                cliente.return_value.fetch.side_effect = RucLookupError(error)
            else:
                cliente.return_value.fetch.return_value = perfil or profile()
            return self.client.post(self.url, datos)

    def test_un_proveedor_marcado_no_entra_sin_que_lo_veas(self):
        response = self.alta(
            perfil=profile(status="BAJA DE OFICIO", condition="NO HABIDO"),
            ruc=PROVEEDOR_NUEVO,
        )
        self.assertEqual(response.status_code, http.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "proveedor_con_observaciones")
        self.assertIn("NO HABIDO", response.data["ruc"])
        self.assertIn("crédito fiscal", response.data["ruc"])
        self.assertFalse(Supplier.objects.filter(ruc=PROVEEDOR_NUEVO).exists())

    def test_se_puede_registrar_a_sabiendas(self):
        """Vigilar a un proveedor con problemas es una razón legítima para
        registrarlo; lo que no vale es hacerlo sin enterarse."""
        response = self.alta(
            perfil=profile(status="BAJA DE OFICIO", condition="NO HABIDO"),
            ruc=PROVEEDOR_NUEVO, accept_risk=True,
        )
        self.assertEqual(response.status_code, http.HTTP_201_CREATED)
        creado = Supplier.objects.get(ruc=PROVEEDOR_NUEVO)
        self.assertTrue(creado.has_issue)
        self.assertEqual(creado.condition, "NO HABIDO")

    def test_el_alta_correcta_deja_la_ficha_y_el_historial_hechos(self):
        response = self.alta(ruc=PROVEEDOR_NUEVO, alias="Nuevo")
        self.assertEqual(response.status_code, http.HTTP_201_CREATED)

        creado = Supplier.objects.get(ruc=PROVEEDOR_NUEVO)
        self.assertEqual(creado.business_name, "SUPERMERCADOS PERUANOS")
        self.assertEqual(creado.status, "ACTIVO")
        self.assertFalse(creado.has_issue)
        self.assertIsNotNone(creado.last_checked_at)
        self.assertEqual(creado.checks.count(), 1)

    def test_si_sunat_no_responde_el_alta_sigue_con_el_error_anotado(self):
        """Quedarse sin poder registrar a un proveedor porque el portal está
        caído sería peor que registrarlo sin verificar."""
        response = self.alta(error="SUNAT no responde", ruc=PROVEEDOR_NUEVO)
        self.assertEqual(response.status_code, http.HTTP_201_CREATED)
        creado = Supplier.objects.get(ruc=PROVEEDOR_NUEVO)
        self.assertIn("SUNAT no responde", creado.last_error)
        self.assertIsNone(creado.last_checked_at)

    def test_el_alta_consulta_una_sola_vez(self):
        """La ficha y el primer punto del historial salen de la misma consulta."""
        with patch("suppliers.views.RucLookupClient") as cliente:
            cliente.return_value.fetch.return_value = profile()
            self.client.post(self.url, {"ruc": PROVEEDOR_NUEVO})
        self.assertEqual(cliente.return_value.fetch.call_count, 1)


class DescubrirProveedoresTests(TenantAPITestCase):
    def setUp(self):
        self.url = reverse("suppliers:supplier-discover")

    def test_propone_a_quien_le_compras_ordenado_por_monto(self):
        comprobante(RUC_ACTIVE, "1000.00")
        comprobante(PROVEEDOR_CARO, "5000.00")

        filas = self.client.get(self.url).data["results"]
        self.assertEqual([f["ruc"] for f in filas], [PROVEEDOR_CARO, RUC_ACTIVE])
        self.assertEqual(Decimal(filas[0]["total"]), Decimal("5000.00"))

    def test_las_notas_de_credito_se_restan(self):
        """Una factura anulada por nota de crédito no es exposición real."""
        comprobante(RUC_ACTIVE, "1000.00")
        comprobante(RUC_ACTIVE, "400.00", document_class=DocumentClass.CREDIT_NOTE)

        fila = self.client.get(self.url).data["results"][0]
        self.assertEqual(Decimal(fila["total"]), Decimal("600.00"))

    def test_no_propone_lo_que_ya_vigilas_ni_a_ti_mismo(self):
        create_supplier(ruc=RUC_ACTIVE)
        comprobante(RUC_ACTIVE, "1000.00")
        comprobante(DEFAULT_RUC, "900.00")

        self.assertEqual(self.client.get(self.url).data["results"], [])

    def test_alta_masiva_incorpora_sin_duplicar(self):
        comprobante(PROVEEDOR_CARO, "5000.00")
        create_supplier(ruc=RUC_ACTIVE)

        response = self.client.post(
            self.url, {"rucs": [PROVEEDOR_CARO, RUC_ACTIVE, PROVEEDOR_CARO]}, format="json"
        )
        self.assertEqual(response.status_code, http.HTTP_201_CREATED)
        self.assertEqual(response.data["added"], 1)
        self.assertEqual(Supplier.objects.filter(account_ruc=DEFAULT_RUC).count(), 2)

        incorporado = Supplier.objects.get(ruc=PROVEEDOR_CARO)
        self.assertEqual(incorporado.business_name, f"PROVEEDOR {PROVEEDOR_CARO}")
        self.assertIsNone(incorporado.last_checked_at)


class ExposicionEnLaListaTests(TenantAPITestCase):
    def test_la_lista_ordena_por_riesgo_y_por_dinero(self):
        barato = create_supplier(ruc=RUC_ACTIVE, alias="Barato")
        caro = create_supplier(ruc=PROVEEDOR_CARO, alias="Caro")
        marcado = create_supplier(ruc=PROVEEDOR_NUEVO, alias="Marcado")
        marcado.has_issue = True
        marcado.save()

        comprobante(barato.ruc, "100.00")
        comprobante(caro.ruc, "9000.00")

        filas = self.client.get(reverse("suppliers:supplier-list")).data["results"]
        # Primero el marcado; después, por dinero.
        self.assertEqual([f["ruc"] for f in filas], [marcado.ruc, caro.ruc, barato.ruc])
        self.assertEqual(Decimal(filas[1]["purchases_total"]), Decimal("9000.00"))
        self.assertEqual(filas[1]["purchases_count"], 1)
        self.assertIsNone(filas[0]["purchases_total"])


class RiesgoCreditoFiscalTests(TenantAPITestCase):
    def setUp(self):
        self.url = reverse("suppliers:supplier-tax-credit-risk")
        self.marcado = create_supplier(ruc=RUC_ACTIVE, alias="Riesgoso")
        self.marcado.status = "BAJA DE OFICIO"
        self.marcado.condition = "NO HABIDO"
        self.marcado.has_issue = True
        self.marcado.save()

    def test_suma_el_igv_en_juego_de_los_proveedores_marcados(self):
        comprobante(self.marcado.ruc, "1180.00")

        datos = self.client.get(self.url).data["totales"]
        self.assertEqual(datos["proveedores"], 1)
        self.assertEqual(datos["comprobantes"], 1)
        self.assertEqual(Decimal(datos["total"]), Decimal("1180.00"))
        # 18% incluido en el total: 1180 → 180.
        self.assertEqual(Decimal(datos["igv_estimado"]), Decimal("180.00"))

    def test_ignora_a_los_proveedores_sanos(self):
        sano = create_supplier(ruc=PROVEEDOR_CARO, alias="Sano")
        comprobante(sano.ruc, "5000.00")

        datos = self.client.get(self.url).data["totales"]
        self.assertEqual(datos["comprobantes"], 0)
        self.assertEqual(Decimal(datos["total"]), Decimal("0.00"))

    def test_distingue_lo_demostrable_de_lo_que_solo_hay_que_revisar(self):
        """Si ya constaba marcado antes de la factura, el problema se puede
        demostrar; si cayó después, la factura puede estar perfectamente bien.
        Mezclarlos daría una cifra alarmista e inservible."""
        SupplierCheck.objects.create(
            supplier=self.marcado, checked_on=date(2026, 7, 1),
            status="BAJA DE OFICIO", condition="NO HABIDO",
            has_issue=True, succeeded=True,
        )
        comprobante(self.marcado.ruc, "1180.00", issue_date=date(2026, 7, 15))
        comprobante(
            self.marcado.ruc, "590.00",
            issue_date=date(2026, 6, 1), series="F002",
        )

        respuesta = self.client.get(self.url).data
        self.assertEqual(respuesta["totales"]["comprobantes"], 2)
        self.assertEqual(respuesta["totales"]["confirmados"], 1)

        por_fecha = {f["fecha"]: f for f in respuesta["facturas"]["results"]}
        self.assertTrue(por_fecha["2026-07-15"]["confirmado_en_la_fecha"])
        self.assertEqual(por_fecha["2026-07-15"]["condicion_en_la_fecha"], "NO HABIDO")
        # Anterior a cualquier consulta: no se afirma que estuviera bien.
        self.assertFalse(por_fecha["2026-06-01"]["confirmado_en_la_fecha"])
        self.assertEqual(por_fecha["2026-06-01"]["condicion_en_la_fecha"], "")

    def test_no_cruza_empresas(self):
        otra = "20200000002"
        Supplier.objects.create(
            account_ruc=otra, ruc=PROVEEDOR_CARO, has_issue=True,
            status="BAJA DE OFICIO", condition="NO HABIDO",
        )
        comprobante(PROVEEDOR_CARO, "9999.00", account_ruc=otra)

        datos = self.client.get(self.url).data["totales"]
        self.assertEqual(datos["comprobantes"], 0)


class PrimeraSincronizacionTests(TenantAPITestCase):
    """Quien acaba de registrarse no tiene proveedores dados de alta, así que
    sin esto el paso de proveedores no haría nada justo en la sincronización
    que más importa: la primera."""

    def setUp(self):
        from types import SimpleNamespace

        self.creds = SimpleNamespace(
            ruc=DEFAULT_RUC, username="CONSULTA1", password="clave"
        )

    def test_la_carga_inicial_puebla_la_cartera_desde_las_compras(self):
        from sync.sources import Cadence, _suppliers

        comprobante(RUC_ACTIVE, "1000.00")
        comprobante(PROVEEDOR_CARO, "5000.00")

        with patch("suppliers.services.monitor.RucLookupClient") as cliente:
            cliente.return_value.fetch.return_value = profile()
            detalle = _suppliers(self.creds, Cadence.INITIAL)

        self.assertEqual(detalle["incorporados"], 2)
        self.assertEqual(detalle["revisados"], 2)
        self.assertEqual(
            Supplier.objects.filter(account_ruc=DEFAULT_RUC).count(), 2
        )
        # Y quedan consultados en la misma corrida, no «nunca consultado».
        self.assertFalse(
            Supplier.objects.filter(
                account_ruc=DEFAULT_RUC, last_checked_at__isnull=True
            ).exists()
        )

    def test_las_sincronizaciones_siguientes_tambien_incorporan(self):
        """Quien te factura es tu proveedor: entra en cualquier cadencia. Lo
        que el usuario dejó de vigilar conserva su ficha y no se rehace."""
        from sync.sources import Cadence, _suppliers

        comprobante(RUC_ACTIVE, "1000.00")
        comprobante(PROVEEDOR_CARO, "9000.00")
        create_supplier(ruc=PROVEEDOR_CARO, is_tracked=False)  # dado de baja

        with patch("suppliers.services.monitor.RucLookupClient") as cliente:
            cliente.return_value.fetch.return_value = profile()
            detalle = _suppliers(self.creds, Cadence.DAILY)

        self.assertEqual(detalle["incorporados"], 1)
        self.assertTrue(Supplier.objects.get(ruc=RUC_ACTIVE).is_tracked)
        self.assertFalse(Supplier.objects.get(ruc=PROVEEDOR_CARO).is_tracked)

    def test_la_carga_inicial_respeta_el_tope(self):
        from suppliers.services import incorporar_desde_compras

        for ruc in (RUC_ACTIVE, PROVEEDOR_CARO, PROVEEDOR_NUEVO):
            comprobante(ruc, "1000.00")

        self.assertEqual(incorporar_desde_compras(DEFAULT_RUC, tope=2), 2)
        self.assertEqual(Supplier.objects.count(), 2)


class PaginacionTests(TenantAPITestCase):
    """Las listas se paginan; los totales, no.

    Un importe que cambiara al pasar de página no serviría para decidir nada,
    así que el resumen de riesgo se calcula sobre el conjunto completo aunque
    solo se enseñen unos pocos comprobantes.
    """

    def test_el_descubrimiento_pagina(self):
        # `override_settings` no sirve aquí: DRF fija `page_size` al definir la
        # clase, así que se ajusta sobre la clase misma.
        from rest_framework.pagination import PageNumberPagination

        for ruc in (RUC_ACTIVE, PROVEEDOR_CARO, PROVEEDOR_NUEVO):
            comprobante(ruc, "1000.00")

        url = reverse("suppliers:supplier-discover")
        with patch.object(PageNumberPagination, "page_size", 2):
            datos = self.client.get(url).data

        self.assertEqual(datos["count"], 3)
        self.assertEqual(len(datos["results"]), 2)
        self.assertIsNotNone(datos["next"])

    def test_los_totales_de_riesgo_no_dependen_de_la_pagina(self):
        marcado = create_supplier(ruc=RUC_ACTIVE, alias="Riesgoso")
        marcado.status, marcado.condition = "BAJA DE OFICIO", "NO HABIDO"
        marcado.has_issue = True
        marcado.save()
        for i in range(3):
            comprobante(marcado.ruc, "1180.00", series=f"F00{i}")

        url = reverse("suppliers:supplier-tax-credit-risk")
        primera = self.client.get(url).data
        segunda = self.client.get(f"{url}?page=1").data

        self.assertEqual(primera["totales"], segunda["totales"])
        self.assertEqual(primera["totales"]["comprobantes"], 3)
        self.assertEqual(Decimal(primera["totales"]["total"]), Decimal("3540.00"))
        self.assertEqual(Decimal(primera["totales"]["igv_estimado"]), Decimal("540.00"))
        self.assertEqual(primera["facturas"]["count"], 3)
