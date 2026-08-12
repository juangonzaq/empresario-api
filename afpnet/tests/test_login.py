"""El login de AFPnet, con la persona en medio.

Lo que se comprueba aquí no es que sepamos entrar —eso depende del portal— sino
que las tres cosas que pueden salir mal se distingan entre sí:

* el CAPTCHA estaba mal → se ofrece otra imagen, las credenciales siguen bien;
* las credenciales estaban mal → **no** se reintenta, porque insistir bloquea
  el usuario de la empresa en AFPnet;
* la pantalla cambió → se dice, en vez de fallar más adelante con un error que
  no se parece a la causa.
"""

from __future__ import annotations

import base64
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from accounts.models import Organization
from afpnet.models import AfpnetSession, SessionStatus
from afpnet.services import client

RUC = "20604442533"

IMAGEN = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"

LOGIN_HTML = f"""
<html><body>
  <form id="frm-inicio-sesion" method="post">
    <input id="TipoUsuario" name="TipoUsuario" value="2" />
    <input id="NumeroDocumento" name="NumeroDocumento" />
    <input id="NombreUsuario" name="NombreUsuario" />
    <input id="Contrasenia" name="Contrasenia" type="password" />
    <input id="Captcha" name="Captcha" />
    <input id="CaptchaValidate" name="CaptchaValidate" value="ABC[YSH3]DEF" />
    <img id="CaptchaImg" src="{IMAGEN}" />
    <input name="__RequestVerificationToken" value="tok-123" />
  </form>
</body></html>
"""

PORTAL_HTML = "<html><body><h1>Bandeja del empleador</h1></body></html>"

# Copiada de la que devuelve AFPnet fuera de su horario, con HTTP 200.
MANTENIMIENTO_HTML = """
<html><head><title>.:: Sistema No Disponible - AFPnet ::.</title></head>
<body>
  <h1>SISTEMA NO DISPONIBLE</h1>
  <p>En este momento no podemos atenderlo. AFPnet est&aacute; disponible todos
     los d&iacute;as desde las 05:02 a. m. hasta las 11:00 p. m.</p>
</body></html>
"""


class RespuestaFalsa:
    def __init__(self, text: str, cookies: dict | None = None):
        self.text = text
        self._cookies = cookies or {}

    def raise_for_status(self):
        return None


def _con_get(html: str):
    """Parchea la sesión HTTP para que GET devuelva `html`."""
    return patch.object(
        client.requests.Session, "get",
        lambda self, *a, **k: RespuestaFalsa(html),
    )


def _con_post(html: str, cookies: dict | None = None):
    def post(self, *a, **k):
        for nombre, valor in (cookies or {}).items():
            self.cookies.set(nombre, valor, domain="www.afpnet.com.pe")
        return RespuestaFalsa(html)

    return patch.object(client.requests.Session, "post", post)


class DesafioTests(SimpleTestCase):
    def test_devuelve_la_imagen_y_los_tokens(self):
        with _con_get(LOGIN_HTML):
            desafio = client.pedir_desafio()

        self.assertEqual(desafio.captcha_data_uri, IMAGEN)
        self.assertEqual(desafio.estado["token"], "tok-123")
        self.assertEqual(desafio.estado["captcha_validate"], "ABC[YSH3]DEF")

    def test_la_imagen_se_limpia_de_entidades_html(self):
        """El portal mete `&#xD;` y saltos de línea dentro del base64. El
        navegador los ignora; `b64decode` revienta con «Incorrect padding»,
        que no se parece en nada a la causa."""
        sucio = IMAGEN.replace("base64,", "base64,&#xD;\n  ")
        with _con_get(LOGIN_HTML.replace(IMAGEN, sucio)):
            desafio = client.pedir_desafio()

        _, _, datos = desafio.captcha_data_uri.partition(",")
        self.assertEqual(base64.b64decode(datos)[:3], b"GIF")

    def test_el_portal_cerrado_no_se_confunde_con_un_cambio_de_web(self):
        """AFPnet cierra de noche y devuelve su propia pantalla con HTTP 200.
        Sin distinguirla, su horario se leía como «la web cambió», que manda a
        alguien a depurar un problema que no existe."""
        with _con_get(MANTENIMIENTO_HTML):
            with self.assertRaises(client.PortalCerrado) as ctx:
                client.pedir_desafio()

        mensaje = str(ctx.exception)
        self.assertIn("cerrado", mensaje.lower())
        # El horario se cita del propio portal, no se codifica en el nuestro.
        self.assertIn("05:02", mensaje)

    def test_si_la_pantalla_cambia_se_dice_aqui(self):
        with _con_get("<html><body>Portal en mantenimiento</body></html>"):
            with self.assertRaises(client.AfpnetError) as ctx:
                client.pedir_desafio()

        self.assertIn("no tiene la forma que esperábamos", str(ctx.exception))


class RespuestaAlDesafioTests(SimpleTestCase):
    def setUp(self):
        self.estado = {
            "token": "tok-123", "captcha_validate": "ABC", "cookies": {},
        }

    def test_entrar_devuelve_las_cookies(self):
        with _con_post(PORTAL_HTML, {"ASP.NET_SessionId": "s3si0n"}):
            cookies = client.responder_desafio(
                self.estado, RUC, "ADM0001", "clave", "VCBL"
            )

        self.assertEqual(cookies["ASP.NET_SessionId"], "s3si0n")

    def test_captcha_malo_se_marca_como_reintentable(self):
        html = LOGIN_HTML.replace(
            "</form>", "<span>El código de la imagen es incorrecto</span></form>"
        )
        with _con_post(html):
            with self.assertRaises(client.LoginRechazado) as ctx:
                client.responder_desafio(
                    self.estado, RUC, "ADM0001", "clave", "malo"
                )

        self.assertTrue(ctx.exception.captcha)

    def test_credenciales_malas_no_se_marcan_como_reintentables(self):
        """Distinguirlas importa: reintentar con la clave mala bloquea el
        usuario de la empresa en AFPnet."""
        html = LOGIN_HTML.replace(
            "</form>", "<span>Usuario o contraseña incorrectos</span></form>"
        )
        with _con_post(html):
            with self.assertRaises(client.LoginRechazado) as ctx:
                client.responder_desafio(
                    self.estado, RUC, "ADM0001", "mala", "VCBL"
                )

        self.assertFalse(ctx.exception.captcha)

    def test_un_desafio_sin_token_no_se_envia(self):
        with self.assertRaises(client.DesafioCaducado):
            client.responder_desafio({}, RUC, "ADM0001", "clave", "VCBL")

    def test_la_clave_no_viaja_en_el_estado(self):
        """El estado va a la caché entre las dos peticiones; que no lleve
        secretos de la empresa es lo que hace ese salto aceptable."""
        with _con_get(LOGIN_HTML):
            desafio = client.pedir_desafio()

        self.assertNotIn("clave", str(desafio.estado).lower())
        self.assertNotIn("contrasenia", str(desafio.estado).lower())


class SesionVivaTests(SimpleTestCase):
    def test_la_pantalla_de_login_significa_sesion_caducada(self):
        """AFPnet responde 200 con el formulario de login cuando la sesión
        murió: mirar solo el código de estado daría la sesión por buena."""
        with _con_get(LOGIN_HTML):
            with self.assertRaises(client.SesionCaducada):
                client.comprobar_viva({"c": "1"}, f"{client.BASE}/Empleador/Inicio")

    def test_una_pagina_del_portal_confirma_que_sigue_viva(self):
        with _con_get(PORTAL_HTML):
            html = client.comprobar_viva({"c": "1"}, f"{client.BASE}/Empleador/Inicio")

        self.assertIn("Bandeja del empleador", html)


class SesionGuardadaTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(ruc=RUC, name="PATTERN")

    def test_las_cookies_se_guardan_cifradas(self):
        sesion = AfpnetSession.objects.create(
            organization=self.organization, taxpayer_id=RUC
        )
        sesion.marcar_activa({"ASP.NET_SessionId": "s3cr3t0"}, "ADM0001")

        crudo = AfpnetSession.objects.values_list(
            "encrypted_cookies", flat=True
        ).get(pk=sesion.pk)
        self.assertNotIn("s3cr3t0", crudo)
        self.assertEqual(sesion.cookies["ASP.NET_SessionId"], "s3cr3t0")

    def test_una_sesion_recien_abierta_es_usable(self):
        sesion = AfpnetSession.objects.create(
            organization=self.organization, taxpayer_id=RUC
        )
        sesion.marcar_activa({"c": "1"}, "ADM0001")

        self.assertTrue(sesion.is_usable)
        self.assertEqual(sesion.status, SessionStatus.ACTIVE)

    def test_caducarla_borra_las_cookies(self):
        """Una sesión muerta no debe dejar cookies guardadas: no sirven para
        nada y son un secreto menos que custodiar."""
        sesion = AfpnetSession.objects.create(
            organization=self.organization, taxpayer_id=RUC
        )
        sesion.marcar_activa({"c": "1"}, "ADM0001")

        sesion.marcar_caducada("la sesión expiró")

        self.assertFalse(sesion.is_usable)
        self.assertEqual(sesion.encrypted_cookies, "")
        self.assertEqual(sesion.cookies, {})

    def test_nunca_se_guarda_la_clave(self):
        campos = {f.name for f in AfpnetSession._meta.get_fields()}
        self.assertNotIn("password", campos)
        self.assertNotIn("encrypted_password", campos)


class SesionExpiradaPorInactividadTests(SimpleTestCase):
    """AFPnet no devuelve el login cuando la sesión expira: redirige a su
    propia pantalla de tiempo agotado con un 408. Sin distinguirlo, la
    interfaz decía «no pudimos consultar AFPnet» en vez de «vuelve a entrar»."""

    def test_el_408_se_lee_como_sesion_caducada(self):
        class Respuesta:
            status_code = 408
            url = f"{client.BASE}{client.RUTA_TIEMPO_AGOTADO}"
            text = ""

            def raise_for_status(self):
                raise AssertionError("no debería llegar aquí")

        with patch.object(client.requests.Session, "post",
                          lambda self, *a, **k: Respuesta()):
            with self.assertRaises(client.SesionCaducada):
                client.comprobar_sesion({"c": "1"})

    def test_una_sesion_viva_devuelve_json(self):
        class Respuesta:
            status_code = 200
            url = client.URL_SESION_VIVA
            text = '{"result": []}'

            def raise_for_status(self):
                return None

        with patch.object(client.requests.Session, "post",
                          lambda self, *a, **k: Respuesta()):
            client.comprobar_sesion({"c": "1"})  # no lanza
