"""Las señales que un auditor miraría en tus compras, y la simulación."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from django.urls import reverse

from suppliers.models import Supplier

from core.testing import TenantAPITestCase
from ruc_profile.models import RucSection, RucSnapshot, WorkerHeadcount
from suppliers.services import (
    analizar_proveedor, compatibilidad, parsear_actividades, simular_fiscalizacion,
)

from .factories import RUC_ACTIVE, create_supplier
from .test_supplier_intelligence import PROVEEDOR_NUEVO, comprobante


def claves(analisis) -> set[str]:
    return {s.clave for s in analisis.senales}


class SenalesTests(TenantAPITestCase):
    def test_sin_facturas_no_hay_senales(self):
        supplier = create_supplier(ruc=RUC_ACTIVE)
        analisis = analizar_proveedor(supplier)
        self.assertEqual(analisis.senales, [])
        self.assertEqual(analisis.nivel, "sin_senales")

    def test_rafaga_de_facturas_el_mismo_dia(self):
        supplier = create_supplier(ruc=RUC_ACTIVE)
        for _ in range(5):
            comprobante(RUC_ACTIVE, "1180.00", issue_date=date(2026, 3, 10))
        comprobante(RUC_ACTIVE, "500.00", issue_date=date(2026, 4, 2))

        analisis = analizar_proveedor(supplier)
        senal = next(s for s in analisis.senales if s.clave == "mismo_dia")
        self.assertEqual(senal.gravedad, "alta")
        self.assertEqual(senal.comprobantes, 5)
        self.assertIn("10/03/2026", senal.detalle)
        self.assertEqual(senal.importe, Decimal("5900.00"))

    def test_dos_facturas_el_mismo_dia_no_es_rafaga(self):
        supplier = create_supplier(ruc=RUC_ACTIVE)
        comprobante(RUC_ACTIVE, "100.00", issue_date=date(2026, 3, 10))
        comprobante(RUC_ACTIVE, "100.00", issue_date=date(2026, 3, 10))
        self.assertNotIn("mismo_dia", claves(analizar_proveedor(supplier)))

    def test_proveedor_que_factura_recien_inscrito(self):
        supplier = create_supplier(
            ruc=RUC_ACTIVE, started_activities_on=date(2025, 5, 28),
        )
        comprobante(RUC_ACTIVE, "3000.00", issue_date=date(2025, 6, 10))

        senal = next(
            s for s in analizar_proveedor(supplier).senales
            if s.clave == "proveedor_reciente"
        )
        self.assertEqual(senal.gravedad, "alta")  # 13 días después
        self.assertIn("28/05/2025", senal.detalle)

    def test_proveedor_veterano_no_es_reciente(self):
        supplier = create_supplier(
            ruc=RUC_ACTIVE, started_activities_on=date(2010, 1, 1),
        )
        comprobante(RUC_ACTIVE, "3000.00", issue_date=date(2025, 6, 10))
        self.assertNotIn("proveedor_reciente", claves(analizar_proveedor(supplier)))

    def test_suspendido_poco_despues_de_facturar(self):
        supplier = create_supplier(
            ruc=RUC_ACTIVE, status="SUSPENSION TEMPORAL", condition="HABIDO",
            has_issue=True,
            last_changed_at=datetime(2025, 9, 1, tzinfo=timezone.utc),
        )
        comprobante(RUC_ACTIVE, "1000.00", issue_date=date(2025, 7, 20))

        senal = next(
            s for s in analizar_proveedor(supplier).senales
            if s.clave == "baja_tras_facturar"
        )
        self.assertEqual(senal.gravedad, "alta")
        self.assertIn("SUSPENSION TEMPORAL", senal.detalle)

    def test_baja_muy_posterior_no_se_asocia(self):
        supplier = create_supplier(
            ruc=RUC_ACTIVE, status="BAJA DE OFICIO", has_issue=True,
            last_changed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        comprobante(RUC_ACTIVE, "1000.00", issue_date=date(2024, 1, 20))
        self.assertNotIn("baja_tras_facturar", claves(analizar_proveedor(supplier)))

    def test_numeracion_correlativa(self):
        supplier = create_supplier(ruc=RUC_ACTIVE)
        for i, n in enumerate((12, 13, 14, 15, 16)):
            comprobante(
                RUC_ACTIVE, "250.50", number=str(n),
                issue_date=date(2026, 1, 5 + i),
            )
        senal = next(
            s for s in analizar_proveedor(supplier).senales
            if s.clave == "correlativas"
        )
        self.assertIn("5 de 5", senal.detalle)

    def test_numeracion_con_huecos_no_es_correlativa(self):
        supplier = create_supplier(ruc=RUC_ACTIVE)
        for i, n in enumerate((10, 40, 90, 150, 220)):
            comprobante(
                RUC_ACTIVE, "250.50", number=str(n),
                issue_date=date(2026, 1, 5 + i),
            )
        self.assertNotIn("correlativas", claves(analizar_proveedor(supplier)))

    def test_montos_redondos_y_cierre_de_ejercicio(self):
        supplier = create_supplier(ruc=RUC_ACTIVE)
        comprobante(RUC_ACTIVE, "5000.00", issue_date=date(2025, 12, 20))
        comprobante(RUC_ACTIVE, "3000.00", issue_date=date(2025, 12, 22))
        comprobante(RUC_ACTIVE, "700.00", issue_date=date(2025, 11, 28))
        comprobante(RUC_ACTIVE, "123.45", issue_date=date(2025, 3, 3))

        senales = claves(analizar_proveedor(supplier))
        self.assertIn("montos_redondos", senales)
        self.assertIn("cierre_ejercicio", senales)

    def test_las_anuladas_no_cuentan(self):
        supplier = create_supplier(ruc=RUC_ACTIVE)
        for _ in range(4):
            comprobante(
                RUC_ACTIVE, "100.00", issue_date=date(2026, 3, 10),
                is_cancelled=True,
            )
        self.assertEqual(analizar_proveedor(supplier).comprobantes, 0)


    def test_no_habido_es_senal_alta(self):
        supplier = create_supplier(
            ruc=RUC_ACTIVE, status="ACTIVO", condition="NO HABIDO", has_issue=True,
        )
        comprobante(RUC_ACTIVE, "1000.00", issue_date=date(2026, 1, 20))
        senal = next(
            s for s in analizar_proveedor(supplier).senales if s.clave == "no_habido"
        )
        self.assertEqual(senal.gravedad, "alta")
        self.assertIn("domicilio fiscal", senal.detalle)

    def test_habido_no_genera_senal(self):
        supplier = create_supplier(ruc=RUC_ACTIVE, status="ACTIVO", condition="HABIDO")
        comprobante(RUC_ACTIVE, "1000.00", issue_date=date(2026, 1, 20))
        self.assertNotIn("no_habido", claves(analizar_proveedor(supplier)))


FERRETERIA = (
    "Principal - 4663 - VENTA AL POR MAYOR DE MATERIALES DE CONSTRUCCIÓN, "
    "ARTÍCULOS DE FERRETERÍA Y EQUIPO Y MATERIALES DE FONTANERÍA Y CALEFACCIÓN "
    "Secundaria 1 - 2023 - FABRICACIÓN DE JABONES Y DETERGENTES"
)
SOFTWARE = "Principal - CIIU 72200 - CONSULTORES PROG. Y SUMINIS. INFORMATICOS."
TELECOM = (
    "Principal - CIIU 64207 - TELECOMUNICACIONES "
    "Secundaria 1 - CIIU 60214 - OTROS TIPOS TRANSPORTE REG. VIA TER."
)


class ActividadTests(TenantAPITestCase):
    def test_parsea_las_dos_formas_de_sunat(self):
        nuevas = parsear_actividades(FERRETERIA)
        self.assertEqual([a.codigo for a in nuevas], ["4663", "2023"])
        self.assertEqual(nuevas[0].rol, "Principal")
        self.assertTrue(nuevas[0].descripcion.startswith("VENTA AL POR MAYOR"))

        viejas = parsear_actividades(TELECOM)
        self.assertEqual([a.codigo for a in viejas], ["64207", "60214"])
        self.assertEqual(viejas[1].rol, "Secundaria 1")
        self.assertEqual(viejas[1].descripcion, "OTROS TIPOS TRANSPORTE REG. VIA TER")

    def test_ferreteria_no_encaja_con_telecomunicaciones(self):
        cruce = compatibilidad(TELECOM, FERRETERIA)
        self.assertFalse(cruce.compatible)
        self.assertIn("materiales de construcción", cruce.motivo)

    def test_los_insumos_universales_siempre_encajan(self):
        self.assertTrue(compatibilidad(FERRETERIA, SOFTWARE).compatible)
        self.assertTrue(compatibilidad(TELECOM, SOFTWARE).compatible)

    def test_mismo_sector_encaja(self):
        constructora = "Principal - 4100 - CONSTRUCCIÓN DE EDIFICIOS"
        self.assertTrue(compatibilidad(constructora, FERRETERIA).compatible)

    def test_sin_datos_no_opina(self):
        self.assertTrue(compatibilidad("", FERRETERIA).compatible)
        self.assertTrue(compatibilidad(TELECOM, "").compatible)

    def test_la_senal_usa_la_ficha_ruc_de_la_empresa(self):
        RucSnapshot.objects.create(
            ruc=self.RUC, captured_on=date(2026, 8, 1), economic_activities=TELECOM,
        )
        supplier = create_supplier(ruc=RUC_ACTIVE, economic_activities=FERRETERIA)
        comprobante(RUC_ACTIVE, "1000.00", issue_date=date(2026, 1, 20))

        analisis = analizar_proveedor(supplier)
        senal = next(s for s in analisis.senales if s.clave == "actividad_ajena")
        self.assertEqual(senal.gravedad, "media")
        self.assertTrue(analisis.actividad_principal.startswith("VENTA AL POR MAYOR"))

    def test_sin_ficha_propia_no_hay_senal_de_actividad(self):
        supplier = create_supplier(ruc=RUC_ACTIVE, economic_activities=FERRETERIA)
        comprobante(RUC_ACTIVE, "1000.00", issue_date=date(2026, 1, 20))
        self.assertNotIn("actividad_ajena", claves(analizar_proveedor(supplier)))


MUEBLES = "Principal - 3100 - FABRICACION DE MUEBLES."
CONSULTORIA = "Principal - 7020 - ACTIVIDADES DE CONSULTORIA DE GESTION."


def ficha_proveedor(
    ruc: str, *, planilla: list[tuple[str, int, int]] | None = (), anexos: int | None = 0,
    actividades: str = MUEBLES,
) -> RucSnapshot:
    """``planilla=()`` = sección capturada sin filas (SUNAT no registra a
    nadie); ``planilla=None`` = la sección no se capturó."""
    ficha = RucSnapshot.objects.create(
        ruc=ruc, captured_on=date(2026, 8, 1), economic_activities=actividades,
        branch_count=anexos,
    )
    if planilla is not None:
        RucSection.objects.create(snapshot=ficha, key="workers", has_data=bool(planilla))
        for periodo, trabajadores, prestadores in planilla:
            WorkerHeadcount.objects.create(
                snapshot=ficha, period=periodo, workers=trabajadores,
                service_providers=prestadores,
            )
    return ficha


class CapacidadOperativaTests(TenantAPITestCase):
    """Los criterios SSCO que sí tienen fuente pública: personal, local y volumen."""

    def test_sin_personal_ni_local_con_volumen_alto(self):
        ficha_proveedor(RUC_ACTIVE)  # planilla capturada vacía, 0 anexos, fabrica muebles
        supplier = create_supplier(ruc=RUC_ACTIVE, economic_activities=MUEBLES)
        for mes in (3, 5, 7):
            comprobante(RUC_ACTIVE, "30000.00", issue_date=date(2026, mes, 10))

        analisis = analizar_proveedor(supplier)
        self.assertEqual(analisis.trabajadores, 0)
        self.assertEqual(analisis.anexos, 0)
        self.assertLessEqual({"sin_personal", "sin_local", "volumen_desproporcionado"}, claves(analisis))
        self.assertEqual(analisis.nivel, "alto")
        volumen = next(s for s in analisis.senales if s.clave == "volumen_desproporcionado")
        self.assertEqual(volumen.gravedad, "alta")
        self.assertEqual(volumen.importe, Decimal("90000.00"))
        self.assertIn("sin ningún local", volumen.detalle)

    def test_poco_volumen_sin_personal_solo_marca_personal(self):
        ficha_proveedor(RUC_ACTIVE, anexos=1)
        supplier = create_supplier(ruc=RUC_ACTIVE)
        comprobante(RUC_ACTIVE, "2000.00", issue_date=date(2026, 7, 10))
        senales = claves(analizar_proveedor(supplier))
        self.assertIn("sin_personal", senales)
        self.assertNotIn("volumen_desproporcionado", senales)
        self.assertNotIn("sin_local", senales)

    def test_con_planilla_en_los_meses_facturados_no_hay_senal(self):
        ficha_proveedor(RUC_ACTIVE, planilla=[("2026-06", 0, 0), ("2026-07", 4, 1)])
        supplier = create_supplier(ruc=RUC_ACTIVE)
        comprobante(RUC_ACTIVE, "30000.00", issue_date=date(2026, 7, 10))
        analisis = analizar_proveedor(supplier)
        self.assertEqual(analisis.trabajadores, 5)
        self.assertNotIn("sin_personal", claves(analisis))
        self.assertNotIn("volumen_desproporcionado", claves(analisis))

    def test_volumen_por_persona(self):
        # 1 persona y S/ 300 000 en el año: 54 UIT por cabeza, más del doble del umbral.
        ficha_proveedor(RUC_ACTIVE, planilla=[("2026-07", 1, 0)], anexos=1)
        supplier = create_supplier(ruc=RUC_ACTIVE)
        comprobante(RUC_ACTIVE, "300000.00", issue_date=date(2026, 7, 10))
        # Una factura de hace dos años no cuenta para el último año.
        comprobante(RUC_ACTIVE, "999999.00", issue_date=date(2024, 1, 10))
        senal = next(
            s for s in analizar_proveedor(supplier).senales
            if s.clave == "volumen_desproporcionado"
        )
        self.assertEqual(senal.gravedad, "alta")
        self.assertEqual(senal.importe, Decimal("300000.00"))
        self.assertIn("1 persona", senal.detalle)

    def test_consultora_sin_anexos_es_normal(self):
        ficha_proveedor(RUC_ACTIVE, planilla=[("2026-07", 2, 0)], actividades=CONSULTORIA)
        supplier = create_supplier(ruc=RUC_ACTIVE)
        comprobante(RUC_ACTIVE, "5000.00", issue_date=date(2026, 7, 10))
        self.assertNotIn("sin_local", claves(analizar_proveedor(supplier)))

    def test_sin_ficha_o_sin_seccion_no_opina(self):
        supplier = create_supplier(ruc=RUC_ACTIVE)
        comprobante(RUC_ACTIVE, "80000.00", issue_date=date(2026, 7, 10))
        analisis = analizar_proveedor(supplier)
        self.assertIsNone(analisis.trabajadores)
        self.assertFalse({"sin_personal", "sin_local", "volumen_desproporcionado"} & claves(analisis))

        # Ficha capturada pero sin la sección de planilla ni la de anexos.
        ficha_proveedor(RUC_ACTIVE, planilla=None, anexos=None)
        analisis = analizar_proveedor(supplier)
        self.assertIsNone(analisis.trabajadores)
        self.assertFalse({"sin_personal", "sin_local", "volumen_desproporcionado"} & claves(analisis))

    def test_la_simulacion_carga_las_fichas_en_lote(self):
        ficha_proveedor(RUC_ACTIVE)
        comprobante(RUC_ACTIVE, "60000.00", issue_date=date(2026, 7, 10))
        resultado = simular_fiscalizacion(self.RUC)
        self.assertEqual(resultado.por_senal.get("sin_personal"), 1)
        self.assertEqual(resultado.por_senal.get("volumen_desproporcionado"), 1)


class SimulacionTests(TenantAPITestCase):
    def test_suma_la_contingencia_solo_de_los_observados(self):
        create_supplier(ruc=RUC_ACTIVE, started_activities_on=date(2010, 1, 1))
        comprobante(RUC_ACTIVE, "1180.00", issue_date=date(2026, 2, 1))
        # Sin ficha en la cartera: aun así se analiza por sus patrones.
        for _ in range(5):
            comprobante(PROVEEDOR_NUEVO, "1180.00", issue_date=date(2026, 3, 10))

        resultado = simular_fiscalizacion(self.RUC)

        self.assertEqual(resultado.proveedores_analizados, 2)
        self.assertEqual(resultado.proveedores_observados, 1)
        self.assertEqual(resultado.comprobantes_observados, 5)
        self.assertEqual(resultado.total_observado, Decimal("5900.00"))
        self.assertEqual(resultado.igv_en_riesgo, Decimal("900.00"))
        self.assertEqual(resultado.renta_en_riesgo, Decimal("1475.00"))  # 5000 × 29,5 %
        self.assertEqual(resultado.multa_estimada, Decimal("1187.50"))
        self.assertEqual(resultado.contingencia_total, Decimal("3562.50"))
        # Numeradas seguidas por el fixture: tambien salta la correlatividad.
        self.assertEqual(resultado.por_senal, {"mismo_dia": 1, "correlativas": 1})
        observado = resultado.proveedores[0]
        self.assertEqual(observado.ruc, PROVEEDOR_NUEVO)
        self.assertIsNone(observado.supplier_id)
        self.assertEqual(observado.proveedor, f"PROVEEDOR {PROVEEDOR_NUEVO}")

    def test_no_mezcla_empresas(self):
        comprobante(PROVEEDOR_NUEVO, "100.00", account_ruc="20100070970")
        self.assertEqual(simular_fiscalizacion(self.RUC).proveedores_analizados, 0)


class EndpointsTests(TenantAPITestCase):
    def test_senales_de_un_proveedor(self):
        supplier = create_supplier(ruc=RUC_ACTIVE)
        for _ in range(3):
            comprobante(RUC_ACTIVE, "100.00", issue_date=date(2026, 3, 10))

        url = reverse("suppliers:supplier-senales", args=[supplier.pk])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["nivel"], "medio")
        self.assertEqual(response.data["senales"][0]["clave"], "mismo_dia")
        self.assertEqual(response.data["supplier_id"], str(supplier.pk))

    def test_simulacion_de_fiscalizacion(self):
        for _ in range(5):
            comprobante(PROVEEDOR_NUEVO, "1180.00", issue_date=date(2026, 3, 10))

        response = self.client.get(reverse("suppliers:supplier-fiscalizacion"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["contingencia_total"], "3562.50")
        self.assertEqual(len(response.data["proveedores"]), 1)
        self.assertEqual(response.data["proveedores"][0]["nivel"], "alto")


class ListaConSenalesTests(TenantAPITestCase):
    def test_la_lista_trae_el_nivel_y_filtra_por_senales(self):
        con = create_supplier(ruc=RUC_ACTIVE, alias="Con señales")
        create_supplier(ruc=PROVEEDOR_NUEVO, alias="Limpio")
        for _ in range(5):
            comprobante(RUC_ACTIVE, "1180.00", issue_date=date(2026, 3, 10))

        todos = self.client.get(reverse("suppliers:supplier-list")).data["results"]
        niveles = {r["ruc"]: (r["nivel_riesgo"], r["senales"]) for r in todos}
        self.assertEqual(niveles[RUC_ACTIVE][0], "alto")
        self.assertGreater(niveles[RUC_ACTIVE][1], 0)
        self.assertEqual(niveles[PROVEEDOR_NUEVO], ("sin_senales", 0))
        self.assertEqual(todos[0]["id"], str(con.pk))  # las señales pesan en el orden

        filtrados = self.client.get(
            reverse("suppliers:supplier-list"), {"con_senales": "true"}
        ).data
        self.assertEqual(filtrados["count"], 1)
        self.assertEqual(filtrados["results"][0]["ruc"], RUC_ACTIVE)


class CarteraAutomaticaTests(TenantAPITestCase):
    def test_quien_te_factura_aparece_en_la_lista_sin_incorporar(self):
        comprobante(PROVEEDOR_NUEVO, "500.00")
        rucs = [r["ruc"] for r in self.client.get(reverse("suppliers:supplier-list")).data["results"]]
        self.assertIn(PROVEEDOR_NUEVO, rucs)
        self.assertTrue(Supplier.objects.get(ruc=PROVEEDOR_NUEVO).is_tracked)

    def test_dejar_de_vigilar_lo_saca_de_la_lista_pero_no_lo_rehace(self):
        comprobante(PROVEEDOR_NUEVO, "500.00")
        self.client.get(reverse("suppliers:supplier-list"))
        supplier = Supplier.objects.get(ruc=PROVEEDOR_NUEVO)

        borrado = self.client.delete(reverse("suppliers:supplier-detail", args=[supplier.pk]))
        self.assertEqual(borrado.status_code, 204)

        rucs = [r["ruc"] for r in self.client.get(reverse("suppliers:supplier-list")).data["results"]]
        self.assertNotIn(PROVEEDOR_NUEVO, rucs)
        supplier.refresh_from_db()
        self.assertFalse(supplier.is_tracked)
        self.assertEqual(Supplier.objects.filter(ruc=PROVEEDOR_NUEVO).count(), 1)

        ignorados = self.client.get(reverse("suppliers:supplier-list"), {"is_tracked": "false"}).data
        self.assertEqual([r["ruc"] for r in ignorados["results"]], [PROVEEDOR_NUEVO])


class ValidacionTests(TenantAPITestCase):
    def test_el_resumen_dice_cuando_se_valido_por_ultima_vez(self):
        from django.utils import timezone

        cuando = timezone.now()
        create_supplier(ruc=RUC_ACTIVE, last_checked_at=cuando)
        create_supplier(ruc=PROVEEDOR_NUEVO)
        data = self.client.get(reverse("suppliers:supplier-summary")).data
        self.assertEqual(data["last_checked_at"], cuando)
        self.assertEqual(data["never_checked"], 1)

    def test_validar_a_pedido_vuelve_a_consultar_aunque_ya_se_mirara_hoy(self):
        from unittest.mock import patch

        from sync.sources import Cadence, _suppliers
        from suppliers.tests.test_monitor import profile

        class Creds:
            ruc = self.RUC
        create_supplier(ruc=RUC_ACTIVE)
        with patch("suppliers.services.monitor.RucLookupClient") as cliente:
            cliente.return_value.fetch.return_value = profile()
            _suppliers(Creds(), Cadence.DAILY)
            _suppliers(Creds(), Cadence.DAILY)   # hoy ya está: no repite
            self.assertEqual(cliente.return_value.fetch.call_count, 1)
            _suppliers(Creds(), Cadence.NEW)     # a pedido: sí
            self.assertEqual(cliente.return_value.fetch.call_count, 2)


class ReportePdfTests(TenantAPITestCase):
    def test_genera_un_pdf_con_la_cartera_y_las_senales(self):
        from pypdf import PdfReader
        from io import BytesIO

        create_supplier(
            ruc=RUC_ACTIVE, alias="Ferretería Sospechosa", status="ACTIVO",
            condition="NO HABIDO", has_issue=True,
        )
        for _ in range(5):
            comprobante(RUC_ACTIVE, "1180.00", issue_date=date(2026, 3, 10))

        response = self.client.get(reverse("suppliers:supplier-reporte"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("proveedores-20604442533-", response["Content-Disposition"])
        texto = " ".join(p.extract_text() for p in PdfReader(BytesIO(response.content)).pages)
        self.assertIn("Informe de proveedores", texto)
        self.assertIn("Ferretería Sospechosa", texto)
        self.assertIn("Varias facturas el mismo", texto)  # el extractor parte la línea
        self.assertIn("Comprobantes de proveedores marcados", texto)

    def test_una_cartera_vacia_tambien_genera(self):
        response = self.client.get(reverse("suppliers:supplier-reporte"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))
