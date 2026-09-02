"""Doble puerta del /admin: captcha + código al correo, paso a paso."""

from __future__ import annotations

from unittest.mock import patch

from django.core import mail
from django.test import Client, TestCase, override_settings

from accounts.models import OneTimeToken, TokenPurpose, User

CLAVE = "s3creta-del-staff"


def _staff(email="admin@pattern.pe", *, staff=True):
    user = User(email=email, is_staff=staff, is_active=True)
    user.set_password(CLAVE)
    user.save()
    return user


def _captcha(exito: bool, **extra):
    """Parcha la verificación contra Google para no salir a la red."""
    payload = {"success": exito, **extra}
    respuesta = type("R", (), {"json": lambda self: payload})()
    return patch("accounts.admin_guard.requests.post", return_value=respuesta)


@override_settings(RECAPTCHA_SITE_KEY="site", RECAPTCHA_SECRET_KEY="secret")
class AdminGuardTests(TestCase):
    def setUp(self):
        self.client = Client(SERVER_NAME="localhost")
        self.user = _staff()

    def _credenciales(self, password=CLAVE, captcha="tok"):
        return self.client.post("/admin/login/", {
            "paso": "credenciales", "username": self.user.email,
            "password": password, "g-recaptcha-response": captcha,
        })

    def test_admin_redirige_al_login_protegido(self):
        r = self.client.get("/admin/")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.url.startswith("/admin/login/"))

    def test_captcha_rechazado_no_manda_codigo(self):
        with _captcha(False):
            r = self._credenciales()
        self.assertContains(r, "No pudimos verificar")
        self.assertEqual(len(mail.outbox), 0)

    def test_v3_puntaje_bajo_o_accion_ajena_rechazan(self):
        # Un token v3 válido pero con pinta de bot (score bajo), y uno emitido
        # para otra acción del sitio: ninguno abre la puerta.
        with _captcha(True, score=0.1, action="admin_login"):
            r = self._credenciales()
        self.assertContains(r, "No pudimos verificar")
        with _captcha(True, score=0.9, action="otro_form"):
            r = self._credenciales()
        self.assertContains(r, "No pudimos verificar")
        with _captcha(True, score=0.9, action="admin_login"):
            r = self._credenciales()
        self.assertContains(r, "Revisa tu correo")

    def test_password_mala_mismo_mensaje_que_no_staff(self):
        with _captcha(True):
            r1 = self._credenciales(password="otra")
        comun = _staff("comun@pattern.pe", staff=False)
        with _captcha(True):
            r2 = self.client.post("/admin/login/", {
                "paso": "credenciales", "username": comun.email,
                "password": CLAVE, "g-recaptcha-response": "tok",
            })
        for r in (r1, r2):
            self.assertContains(r, "Credenciales inválidas")
        self.assertEqual(len(mail.outbox), 0)

    def test_flujo_completo_con_codigo(self):
        with _captcha(True):
            r = self._credenciales()
        self.assertContains(r, "Revisa tu correo")
        self.assertEqual(len(mail.outbox), 1)
        token = OneTimeToken.objects.get(user=self.user, purpose=TokenPurpose.ADMIN_OTP)
        codigo = token.token.split(".")[0]
        self.assertIn(codigo, mail.outbox[0].body)

        # Código equivocado: sigue afuera.
        r = self.client.post("/admin/login/", {"paso": "codigo", "codigo": "000000"})
        self.assertContains(r, "incorrecto")

        r = self.client.post("/admin/login/", {"paso": "codigo", "codigo": codigo})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, "/admin/")
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        token.refresh_from_db()
        self.assertIsNotNone(token.used_at)  # un solo uso

    def test_demasiados_intentos_reinicia(self):
        with _captcha(True):
            self._credenciales()
        for _ in range(5):
            r = self.client.post("/admin/login/", {"paso": "codigo", "codigo": "999999"})
        self.assertContains(r, "Demasiados intentos")
        # La sesión quedó limpia: el paso 2 ya no acepta códigos.
        r = self.client.post("/admin/login/", {"paso": "codigo", "codigo": "999999"})
        self.assertEqual(r.status_code, 302)

    def test_next_seguro_y_next_externo(self):
        with _captcha(True):
            self.client.post("/admin/login/?next=/admin/accounts/user/", {
                "paso": "credenciales", "username": self.user.email,
                "password": CLAVE, "g-recaptcha-response": "tok",
            })
        codigo = OneTimeToken.objects.get(
            user=self.user, purpose=TokenPurpose.ADMIN_OTP,
        ).token.split(".")[0]
        r = self.client.post("/admin/login/", {"paso": "codigo", "codigo": codigo})
        self.assertEqual(r.url, "/admin/accounts/user/")

    @override_settings(RECAPTCHA_SITE_KEY="", RECAPTCHA_SECRET_KEY="")
    def test_sin_llaves_omite_captcha_pero_no_el_codigo(self):
        r = self.client.post("/admin/login/", {
            "paso": "credenciales", "username": self.user.email, "password": CLAVE,
        })
        self.assertContains(r, "Revisa tu correo")
        self.assertEqual(len(mail.outbox), 1)
