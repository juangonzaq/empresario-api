"""Entrar a SOL con clave mala tiene que decir «clave mala», no «ejecuta is not defined»."""

from __future__ import annotations

from unittest.mock import MagicMock

from django.test import SimpleTestCase

from ruc_profile.services.sol_ficha import SolLoginRejected, asegurar_menu_sol
from sync.sources import LoginFailed, SourceFailed, _tributos


class PaginaFalsa:
    def __init__(self, *, sale_del_login=True, menu=True, mensaje="", formulario=False, titulo="SOL"):
        self.sale_del_login = sale_del_login
        self.menu = menu
        self.mensaje = mensaje
        self.formulario = formulario
        self.url = "https://e-menu.sunat.gob.pe/x"
        self._titulo = titulo

    def wait_for_url(self, *_a, **_k):
        if not self.sale_del_login:
            raise TimeoutError("sigue en loginMenuSol")

    def wait_for_load_state(self, *_a, **_k): ...
    def wait_for_timeout(self, *_a, **_k): ...
    def title(self): return self._titulo

    def evaluate(self, expr):
        assert "typeof ejecuta" in expr
        return self.menu

    def locator(self, selector):
        loc = MagicMock()
        if selector == "#txtContrasena":
            loc.first.is_visible.return_value = self.formulario
        else:
            loc.first.is_visible.return_value = bool(self.mensaje) and selector == "#spanMensaje"
            loc.first.inner_text.return_value = self.mensaje
        return loc


class AsegurarMenuTests(SimpleTestCase):
    def test_con_menu_pasa(self):
        asegurar_menu_sol(PaginaFalsa(), 1000)

    def test_si_no_sale_del_login_es_rechazo_con_el_mensaje_de_sol(self):
        with self.assertRaises(SolLoginRejected) as c:
            asegurar_menu_sol(PaginaFalsa(sale_del_login=False, mensaje="Usuario o clave incorrectos"), 1000)
        self.assertIn("incorrectos", str(c.exception))

    def test_sin_menu_y_con_formulario_es_rechazo(self):
        with self.assertRaises(SolLoginRejected):
            asegurar_menu_sol(PaginaFalsa(menu=False, formulario=True), 1000)

    def test_sin_menu_ni_formulario_ni_mensaje_es_fallo_del_portal(self):
        with self.assertRaises(RuntimeError) as c:
            asegurar_menu_sol(PaginaFalsa(menu=False, titulo="Mantenimiento"), 1000)
        self.assertNotIsInstance(c.exception, SolLoginRejected)
        self.assertIn("Mantenimiento", str(c.exception))


class PasoTributosTests(SimpleTestCase):
    def _correr(self, error):
        from unittest.mock import patch

        class Creds:
            ruc = "20604442533"; username = "u"; password = "p"

        with patch("sync.sources.Organization", create=True), \
             patch("accounts.models.Organization.objects") as orgs, \
             patch("accounts.models.SunatCredential.objects") as creds, \
             patch("ruc_profile.services.sol_ficha.sync_regime", side_effect=error):
            orgs.get.return_value = MagicMock()
            creds.get.return_value = MagicMock()
            return _tributos(Creds(), "manual")

    def test_clave_rechazada_corta_el_trabajo(self):
        with self.assertRaises(LoginFailed):
            self._correr(SolLoginRejected("clave mala"))

    def test_otro_error_solo_tumba_el_paso(self):
        with self.assertRaises(SourceFailed):
            self._correr(RuntimeError("el frame no cargó"))
