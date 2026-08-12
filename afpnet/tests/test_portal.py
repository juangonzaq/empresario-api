"""El navegador del portal: qué manda y de dónde saca los tokens.

Nada de red: se parchea la sesión HTTP. Lo que se comprueba es el contrato con
AFPnet —los campos del formulario y el orden de las llamadas—, que es lo que se
rompe cuando el portal cambia.
"""

from __future__ import annotations

import pathlib
from datetime import date
from unittest.mock import patch

from django.test import SimpleTestCase

from afpnet.services import client, portal

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
PAGINA_CON_TOKEN = (
    '<html><form><input name="__RequestVerificationToken" value="tok-1" />'
    '<input id="RucEmpresaEncriptado" name="RucEmpresaEncriptado" '
    'value="RUCENC123" /></form></html>'
)


class Grabadora:
    """Sustituye a requests.Session y guarda lo que se le pidió."""

    def __init__(self, respuesta: str = "<html></html>"):
        self.respuesta = respuesta
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[str] = []
        self.headers = {}
        self.cookies = {}

    def get(self, url, **kwargs):
        self.gets.append(url)
        return _Resp(PAGINA_CON_TOKEN, url)

    def post(self, url, data=None, **kwargs):
        self.posts.append((url, data or kwargs.get("files") or {}))
        return _Resp(self.respuesta, url)


class _Resp:
    def __init__(self, text, url):
        self.text, self.url, self.status_code = text, url, 200

    def raise_for_status(self):
        return None


def _portal(respuesta="<html></html>") -> tuple[portal.Portal, Grabadora]:
    grabadora = Grabadora(respuesta)
    with patch.object(client, "sesion_autenticada", return_value=grabadora):
        p = portal.Portal(cookies={"c": "1"})
    return p, grabadora


class TokensTests(SimpleTestCase):
    def test_el_token_se_saca_de_la_pagina_no_del_navegador(self):
        p, grabadora = _portal()

        p.resumen_situacion("202601", "202612")

        self.assertIn(portal.RUTA_PAGINA_RESUMEN, grabadora.gets[0])
        _, enviado = grabadora.posts[0]
        self.assertEqual(enviado["__RequestVerificationToken"], "tok-1")

    def test_el_ruc_encriptado_se_lee_de_la_pagina_de_planillas(self):
        p, _ = _portal()

        self.assertEqual(p.ruc_encriptado, "RUCENC123")

    def test_el_token_se_pide_una_sola_vez_por_pagina(self):
        """Una sincronización encadena varias consultas; volver a pedir la
        misma página para cada una sería gastar la sesión por gusto."""
        p, grabadora = _portal()

        p.planillas("202601", "202612", afps=("HA", "IN"))

        self.assertEqual(grabadora.gets.count(f"{client.BASE}{portal.RUTA_PLANILLAS}"), 1)
        self.assertEqual(len(grabadora.posts), 2)


class PlanillasTests(SimpleTestCase):
    def test_recorre_todas_las_afp(self):
        """Una empresa puede repartir trabajadores entre administradoras;
        consultar solo una escondería las planillas del resto."""
        p, grabadora = _portal()

        p.planillas("202601", "202612")

        codigos = [datos["CodigoAFP"] for _, datos in grabadora.posts]
        self.assertEqual(codigos, list(portal.CODIGOS_AFP))

    def test_manda_el_rango_de_devengues_completo(self):
        p, grabadora = _portal()

        p.planillas("202601", "202607", afps=("HA",))

        _, datos = grabadora.posts[0]
        self.assertEqual(datos["PeriodoDevengueInicial"], "202601")
        self.assertEqual(datos["PeriodoDevengueFinal"], "202607")
        self.assertEqual(datos["TipoBusqueda"], portal.TIPO_BUSQUEDA_DEVENGUE)
        # Comprobado contra el portal real: puede ir vacío.
        self.assertEqual(datos["IdTabSession"], "")

    def test_lee_las_planillas_de_la_respuesta(self):
        html = (FIXTURES / "planillas_anio.html").read_text()
        p, _ = _portal(html)

        planillas = p.planillas("202601", "202612", afps=("HA",))

        self.assertEqual(len(planillas), 7)


class SesionTests(SimpleTestCase):
    def test_el_408_se_lee_como_sesion_caducada(self):
        p, grabadora = _portal()

        def get_caducado(url, **kwargs):
            r = _Resp("", f"{client.BASE}{client.RUTA_TIEMPO_AGOTADO}")
            r.status_code = 408
            return r

        grabadora.get = get_caducado
        with self.assertRaises(client.SesionCaducada):
            p.datos_empresa()

    def test_volver_al_login_es_sesion_caducada(self):
        p, grabadora = _portal()
        grabadora.get = lambda url, **k: _Resp(
            f'<html><form id="frm-inicio-sesion"></form></html>', url
        )

        with self.assertRaises(client.SesionCaducada):
            p.datos_empresa()


class RangosTests(SimpleTestCase):
    def test_un_rango_por_anio_hasta_hoy(self):
        rangos = portal.anios_hasta_hoy(2024, hoy=date(2026, 8, 12))

        self.assertEqual(rangos, [
            ("202401", "202412"),
            ("202501", "202512"),
            ("202601", "202608"),
        ])

    def test_el_anio_en_curso_se_corta_en_el_mes_actual(self):
        """Pedir devengues futuros no falla, pero gasta una consulta por AFP
        para no traer nada."""
        rangos = portal.anios_hasta_hoy(2026, hoy=date(2026, 3, 5))

        self.assertEqual(rangos, [("202601", "202603")])
