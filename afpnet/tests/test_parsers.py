"""Los parsers, contra respuestas reales de AFPnet.

Las fixtures son capturas literales del portal, con los datos personales
sustituidos. Si AFPnet cambia una pantalla, lo que falla es un test y no una
sincronización nocturna.
"""

from __future__ import annotations

import pathlib
from decimal import Decimal

from django.test import SimpleTestCase

from afpnet.services import parsers

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def leer(nombre: str) -> str:
    return (FIXTURES / nombre).read_text()


class ResumenSituacionTests(SimpleTestCase):
    def setUp(self):
        self.filas = parsers.parsear_resumen_situacion(leer("resumen_situacion.html"))

    def test_lee_la_fila_del_devengue(self):
        self.assertEqual(len(self.filas), 1)
        fila = self.filas[0]
        self.assertEqual(fila.periodo, "202607")
        self.assertEqual(fila.total_op, 5)
        self.assertEqual(fila.op_cierta, 0)
        self.assertEqual(fila.op_presunta, 5)

    def test_sin_obligaciones_con_deuda(self):
        self.assertFalse(self.filas[0].tiene_deuda)


class PlanillasTests(SimpleTestCase):
    def setUp(self):
        self.planillas = parsers.parsear_planillas(leer("planillas_listar.html"))

    def test_no_duplica_por_la_tabla_de_impresion(self):
        """La respuesta trae una copia para imprimir; sin anclar en
        `#gvw-planilla` cada planilla se contaba dos veces."""
        self.assertEqual(len(self.planillas), 1)

    def test_lee_los_campos_de_la_planilla(self):
        p = self.planillas[0]
        self.assertEqual(p.periodo, "202607")
        self.assertEqual(p.afp, "habitat")
        self.assertEqual(p.numero, "2610640231")
        self.assertEqual(p.nominal_fondo, Decimal("1370.00"))
        self.assertEqual(p.nominal_ryr, Decimal("187.69"))
        self.assertEqual(p.estado, "PAGADA")
        self.assertEqual(p.tipo_trabajador, "DEPENDIENTE")
        self.assertEqual(p.banco, "BCP")

    def test_arrastra_el_devengue_de_la_fila_que_agrupa(self):
        """El detalle no repite el devengue: sale de la fila de arriba."""
        self.assertTrue(all(p.periodo for p in self.planillas))

    def test_lee_las_fechas_en_el_formato_del_portal(self):
        p = self.planillas[0]
        self.assertEqual(str(p.fecha_declaracion), "2026-08-07")
        self.assertEqual(str(p.fecha_pago), "2026-08-11")

    def test_reconoce_que_esta_pagada(self):
        self.assertTrue(self.planillas[0].pagada)


class DeudasTests(SimpleTestCase):
    def setUp(self):
        self.reporte = parsers.parsear_deudas(leer("deudas_reporte.txt"))

    def test_lee_la_cabecera_del_reporte(self):
        self.assertEqual(self.reporte.ruc, "20604442533")
        self.assertIn("PATTERN", self.reporte.razon_social)
        self.assertEqual(self.reporte.devengue_maximo, "202606")
        self.assertEqual(str(self.reporte.actualizado_en), "2026-07-31")

    def test_sin_filas_significa_sin_deuda(self):
        """«No debes nada» y «no pudimos consultarlo» no se pueden confundir en
        una pantalla de cumplimiento."""
        self.assertTrue(self.reporte.sin_deuda)
        self.assertEqual(self.reporte.filas, [])

    def test_la_leyenda_no_se_toma_por_datos(self):
        self.assertFalse(any(f.cuspp.startswith("-") for f in self.reporte.filas))

    def test_lee_una_fila_de_deuda(self):
        """Esta empresa no debe nada, así que la fila se inyecta a mano: el
        formato hay que saber leerlo antes de que alguien deba dinero."""
        con_deuda = leer("deudas_reporte.txt").replace(
            "Leyenda:",
            "999999AAAAA1|DNI - 12345678|PEREZ GARCIA MARIA|HABITAT|2026-05|C|"
            "350.00|59.50|0.00|A|3\nLeyenda:",
        )

        reporte = parsers.parsear_deudas(con_deuda)

        self.assertEqual(len(reporte.filas), 1)
        fila = reporte.filas[0]
        self.assertEqual(fila.cuspp, "999999AAAAA1")
        self.assertEqual(fila.afp, "habitat")
        self.assertEqual(fila.periodo, "202605")
        self.assertEqual(fila.tipo, "C")
        self.assertEqual(fila.deuda_fondo, Decimal("350.00"))
        self.assertEqual(fila.estado_cobranza, "A")


class AfiliadoTests(SimpleTestCase):
    def setUp(self):
        self.afiliado = parsers.parsear_afiliado(leer("consultar_afiliado.html"))

    def test_lee_la_ficha(self):
        a = self.afiliado
        self.assertIsNotNone(a)
        self.assertEqual(a.tipo_documento, "DNI")
        self.assertEqual(a.numero_documento, "12345678")
        self.assertEqual(a.cuspp, "999999AAAAA1")
        self.assertEqual(a.afp, "habitat")
        self.assertEqual(a.tipo_comision, "MIXTA")
        self.assertEqual(a.devengue_maximo, "202608")
        self.assertEqual(a.situacion, "Continúa")

    def test_compone_el_nombre_completo(self):
        self.assertEqual(self.afiliado.nombre_completo, "MARIA ELENA PEREZ GARCIA")

    def test_sin_resultados_devuelve_none(self):
        self.assertIsNone(parsers.parsear_afiliado("<html><body>Nada</body></html>"))


class HistorialAportesTests(SimpleTestCase):
    def setUp(self):
        self.aportes = parsers.parsear_historial_aportes(leer("afiliados_op.json"))

    def test_trae_el_historial_completo(self):
        self.assertEqual(len(self.aportes), 25)

    def test_viene_ordenado_por_periodo(self):
        periodos = [a.periodo for a in self.aportes]
        self.assertEqual(periodos, sorted(periodos))
        self.assertEqual(periodos[0], "202408")
        self.assertEqual(periodos[-1], "202608")

    def test_lee_los_importes_del_primer_mes(self):
        a = self.aportes[0]
        self.assertEqual(a.periodo, "202408")
        self.assertEqual(a.remuneracion, Decimal("3500.00"))
        self.assertEqual(a.obligado_fondo, Decimal("350.00"))
        self.assertEqual(a.obligado_seguro, Decimal("59.50"))
        self.assertTrue(a.relacion_laboral)

    def test_distingue_obligacion_cierta_de_presunta(self):
        tipos = {a.tipo for a in self.aportes}
        self.assertTrue(tipos <= {"C", "P"})


class ConversionesTests(SimpleTestCase):
    def test_el_separador_de_miles_no_rompe_el_importe(self):
        """Sin quitar la coma, `Decimal` lanza y un importe de cuatro cifras se
        perdía en silencio."""
        self.assertEqual(parsers.a_decimal("1,370.00"), Decimal("1370.00"))
        self.assertEqual(parsers.a_decimal("12,345,678.90"), Decimal("12345678.90"))

    def test_importes_vacios_son_none_y_no_cero(self):
        """Cero es un importe; vacío es que no lo sabemos."""
        for vacio in ("", "  ", "-", None):
            self.assertIsNone(parsers.a_decimal(vacio))
        self.assertEqual(parsers.a_decimal("0.00"), Decimal("0.00"))

    def test_acepta_los_dos_formatos_de_fecha_del_portal(self):
        self.assertEqual(str(parsers.a_fecha("07/08/2026")), "2026-08-07")
        self.assertEqual(str(parsers.a_fecha("2026-08-07")), "2026-08-07")
        self.assertIsNone(parsers.a_fecha("no es fecha"))

    def test_normaliza_el_periodo(self):
        self.assertEqual(parsers.a_periodo("2026-07"), "202607")
        self.assertEqual(parsers.a_periodo("202607"), "202607")
        self.assertEqual(parsers.a_periodo("2026"), "")


class DatosEmpresaTests(SimpleTestCase):
    def setUp(self):
        self.datos = parsers.parsear_datos_empresa(leer("datos_empresa.html"))

    def test_lee_la_identificacion(self):
        self.assertEqual(self.datos.ruc, "20604442533")
        self.assertEqual(self.datos.razon_social, "PATTERN GROUP S.A.C")

    def test_compone_direccion_y_ubigeo(self):
        self.assertIn("LOS OLIVOS", self.datos.direccion)
        self.assertEqual(self.datos.departamento, "LIMA")
        self.assertEqual(self.datos.distrito, "LINCE")

    def test_lee_al_representante_legal(self):
        self.assertEqual(self.datos.representante, "MARIA ELENA PEREZ GARCIA")
        self.assertEqual(self.datos.representante_documento, "12345678")
        self.assertEqual(self.datos.representante_cargo, "GERENTE GENERAL")

    def test_seleccione_no_se_toma_por_dato(self):
        """El portal deja «Seleccione» en los desplegables vacíos; guardarlo
        sería inventarse una provincia."""
        self.assertNotIn("Seleccione", (
            self.datos.departamento + self.datos.provincia + self.datos.distrito
        ))

    def test_la_segunda_firma_se_lee_del_checked_y_no_del_value(self):
        """ASP.NET pone un checkbox `value="true"` junto a un hidden
        `value="false"`. Mirando el `value`, toda empresa parecería tener
        segunda firma activada; lo que informa es si está marcado."""
        self.assertFalse(self.datos.segunda_firma)

        marcado = leer("datos_empresa.html").replace(
            'name="BolSegundaFirma" value="true"',
            'name="BolSegundaFirma" value="true" checked="checked"', 1,
        )
        self.assertTrue(parsers.parsear_datos_empresa(marcado).segunda_firma)


class PlanillasDeUnAnioTests(SimpleTestCase):
    """La respuesta real de pedir enero-diciembre de una vez."""

    def setUp(self):
        self.planillas = parsers.parsear_planillas(leer("planillas_anio.html"))

    def test_trae_el_anio_completo(self):
        self.assertEqual(len(self.planillas), 7)

    def test_cada_planilla_lleva_su_devengue(self):
        periodos = sorted(p.periodo for p in self.planillas)
        self.assertEqual(periodos[0], "202601")
        self.assertEqual(periodos[-1], "202607")
        self.assertEqual(len(set(periodos)), 7)

    def test_todas_pagadas_con_importe(self):
        self.assertTrue(all(p.pagada for p in self.planillas))
        self.assertTrue(all(p.nominal_fondo for p in self.planillas))
