"""El padrón SSCO: se descarga, se guarda y cruza con las carteras."""

from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
from unittest.mock import patch

import openpyxl
from django.core.management import call_command
from django.urls import reverse

from core.testing import TenantAPITestCase
from suppliers.models import SujetoSinCapacidadOperativa
from suppliers.services import analizar_proveedor, simular_fiscalizacion
from suppliers.services.ssco import PadronSscoError, guardar, parsear
from suppliers.tasks import sync_ssco

from .factories import RUC_ACTIVE, create_supplier
from .test_supplier_intelligence import PROVEEDOR_NUEVO, comprobante

CABECERA = (
    "RUC", "Razón social", "Domicilio fiscal", "Resolución de atribución como SSCO",
    "Fecha de emisión de la resolución de atribución",
    "Fecha en la que la resolución de atribución quedó firme",
    "RUC o documento de identidad del representante legal (1)",
    "Apellidos y nombres del representante legal", "Fecha de publicación (2)",
)


def excel(*filas, cabecera=CABECERA) -> bytes:
    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.append(list(cabecera))
    for fila in filas:
        hoja.append(list(fila))
    buffer = BytesIO()
    libro.save(buffer)
    return buffer.getvalue()


def fila(ruc: str, nombre: str = "EMPRESA FANTASMA S.A.C."):
    # SUNAT escribe el RUC como número, no como texto.
    return (
        int(ruc), nombre, "AV. FALSA 123 - LIMA",
        "Resolución de Intendencia N.º 024-024-0089075/SUNAT",
        datetime(2026, 6, 26), datetime(2026, 7, 14), int(ruc), nombre,
        datetime(2026, 7, 31),
    )


class ParseoTests(TenantAPITestCase):
    def test_lee_las_nueve_columnas(self):
        filas = parsear(excel(fila(RUC_ACTIVE)))
        self.assertEqual(len(filas), 1)
        f = filas[0]
        self.assertEqual(f.ruc, RUC_ACTIVE)
        self.assertEqual(f.razon_social, "EMPRESA FANTASMA S.A.C.")
        self.assertEqual(f.fecha_resolucion, date(2026, 6, 26))
        self.assertEqual(f.fecha_firme, date(2026, 7, 14))
        self.assertEqual(f.fecha_publicacion, date(2026, 7, 31))
        self.assertEqual(f.representante_documento, RUC_ACTIVE)

    def test_salta_filas_sin_ruc(self):
        filas = parsear(excel(("(1) nota al pie",), (None,), fila(RUC_ACTIVE)))
        self.assertEqual([f.ruc for f in filas], [RUC_ACTIVE])

    def test_un_formato_distinto_falla_ruidosamente(self):
        with self.assertRaises(PadronSscoError):
            parsear(excel(fila(RUC_ACTIVE), cabecera=("Código", "Nombre")))


class GuardadoTests(TenantAPITestCase):
    def test_alta_actualizacion_y_retiro(self):
        primero = guardar(parsear(excel(fila(RUC_ACTIVE), fila(PROVEEDOR_NUEVO))))
        self.assertEqual((primero.nuevos, primero.retirados), (2, 0))

        segundo = guardar(parsear(excel(fila(RUC_ACTIVE, "RENOMBRADA S.A.C."))))
        self.assertEqual(segundo.nuevos, 0)
        self.assertEqual(segundo.actualizados, 1)
        self.assertEqual(segundo.retirados, 1)

        retirado = SujetoSinCapacidadOperativa.objects.get(ruc=PROVEEDOR_NUEVO)
        self.assertFalse(retirado.vigente)  # sigue existiendo: estuvo
        self.assertEqual(
            SujetoSinCapacidadOperativa.objects.get(ruc=RUC_ACTIVE).razon_social,
            "RENOMBRADA S.A.C.",
        )

    def test_un_padron_vacio_no_retira_a_nadie(self):
        guardar(parsear(excel(fila(RUC_ACTIVE))))
        guardar([])
        self.assertTrue(SujetoSinCapacidadOperativa.objects.get(ruc=RUC_ACTIVE).vigente)

    def test_vuelve_a_ser_vigente_si_reaparece(self):
        guardar(parsear(excel(fila(RUC_ACTIVE))))
        guardar(parsear(excel(fila(PROVEEDOR_NUEVO))))
        guardar(parsear(excel(fila(RUC_ACTIVE))))
        self.assertTrue(SujetoSinCapacidadOperativa.objects.get(ruc=RUC_ACTIVE).vigente)


class TareaTests(TenantAPITestCase):
    def test_la_tarea_descarga_y_guarda(self):
        with patch("suppliers.services.ssco.descargar", return_value=excel(fila(RUC_ACTIVE))):
            resultado = sync_ssco()
        self.assertEqual(resultado["nuevos"], 1)

    def test_el_comando_hace_lo_mismo(self):
        with patch("suppliers.services.ssco.descargar", return_value=excel(fila(RUC_ACTIVE))):
            call_command("sync_ssco")
        self.assertEqual(SujetoSinCapacidadOperativa.objects.count(), 1)

    def test_el_cron_mensual_esta_sembrado(self):
        from django_celery_beat.models import PeriodicTask

        tarea = PeriodicTask.objects.get(task="suppliers.sync_ssco")
        self.assertTrue(tarea.enabled)
        self.assertEqual(tarea.crontab.day_of_month, "28-31")


class CruceTests(TenantAPITestCase):
    def setUp(self):
        guardar(parsear(excel(fila(RUC_ACTIVE))))

    def test_la_senal_ssco_es_critica_y_basta_sola(self):
        supplier = create_supplier(ruc=RUC_ACTIVE, status="ACTIVO", condition="HABIDO")
        comprobante(RUC_ACTIVE, "1180.00", issue_date=date(2026, 5, 5))

        analisis = analizar_proveedor(supplier)
        senal = analisis.senales[0]
        self.assertEqual(senal.clave, "ssco")
        self.assertEqual(senal.gravedad, "critica")
        self.assertIn("024-024-0089075", senal.detalle)
        self.assertEqual(analisis.nivel, "alto")

    def test_cruza_tambien_a_los_que_no_estan_en_la_cartera(self):
        comprobante(RUC_ACTIVE, "1180.00", issue_date=date(2026, 5, 5))
        resultado = simular_fiscalizacion(self.RUC)
        self.assertEqual(resultado.por_senal.get("ssco"), 1)

    def test_los_retirados_ya_no_cruzan(self):
        guardar(parsear(excel(fila(PROVEEDOR_NUEVO))))  # RUC_ACTIVE sale del padrón
        supplier = create_supplier(ruc=RUC_ACTIVE)
        comprobante(RUC_ACTIVE, "100.00")
        self.assertNotIn("ssco", {s.clave for s in analizar_proveedor(supplier).senales})

    def test_la_lista_marca_en_ssco(self):
        create_supplier(ruc=RUC_ACTIVE)
        create_supplier(ruc=PROVEEDOR_NUEVO, alias="Limpio")
        response = self.client.get(reverse("suppliers:supplier-list"))
        marcas = {r["ruc"]: r["en_ssco"] for r in response.data["results"]}
        self.assertEqual(marcas, {RUC_ACTIVE: True, PROVEEDOR_NUEVO: False})
