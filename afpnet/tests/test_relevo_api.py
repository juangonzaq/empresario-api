"""El relevo del CAPTCHA visto desde la interfaz.

Lo importante no es el camino feliz, sino que la pantalla pueda reaccionar
distinto a cada fallo: ofrecer otra imagen cuando el CAPTCHA salió mal, y
**parar** cuando lo que AFPnet rechazó fueron las credenciales.
"""

from __future__ import annotations

from unittest.mock import patch

from django.core.cache import cache
from django.urls import reverse

from afpnet.models import AfpnetSession, SessionStatus
from afpnet.services import client
from core.testing import TenantAPITestCase

IMAGEN = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
ESTADO = {"token": "tok", "captcha_validate": "cv", "cookies": {"a": "1"}}


class RelevoTests(TenantAPITestCase):
    RUC = "20604442533"

    def setUp(self):
        self.desafio_url = reverse("afpnet:desafio")
        self.conectar_url = reverse("afpnet:conectar")
        self.addCleanup(cache.clear)

    def _pedir_desafio(self) -> str:
        with patch.object(
            client, "pedir_desafio",
            return_value=client.Desafio(captcha_data_uri=IMAGEN, estado=ESTADO),
        ):
            return self.client.post(self.desafio_url).json()["handle"]

    def test_pedir_desafio_devuelve_imagen_y_handle(self):
        with patch.object(
            client, "pedir_desafio",
            return_value=client.Desafio(captcha_data_uri=IMAGEN, estado=ESTADO),
        ):
            respuesta = self.client.post(self.desafio_url)

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["captcha"], IMAGEN)
        self.assertTrue(respuesta.json()["handle"])

    def test_conectar_guarda_la_sesion(self):
        handle = self._pedir_desafio()
        with patch.object(client, "responder_desafio", return_value={"s": "1"}):
            respuesta = self.client.post(self.conectar_url, {
                "handle": handle, "usuario": "ADM0001",
                "clave": "secreta", "captcha": "VCBL",
            }, format="json")

        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual(respuesta.json()["estado"], "activa")
        sesion = AfpnetSession.objects.get(organization=self.organization)
        self.assertEqual(sesion.status, SessionStatus.ACTIVE)
        self.assertEqual(sesion.cookies, {"s": "1"})

    def test_captcha_mal_escrito_se_marca_reintentable(self):
        handle = self._pedir_desafio()
        with patch.object(
            client, "responder_desafio",
            side_effect=client.LoginRechazado("código de la imagen", captcha=True),
        ):
            respuesta = self.client.post(self.conectar_url, {
                "handle": handle, "usuario": "ADM0001",
                "clave": "secreta", "captcha": "mal",
            }, format="json")

        self.assertEqual(respuesta.status_code, 400)
        self.assertTrue(respuesta.json()["reintentable"])
        self.assertEqual(respuesta.json()["code"], "captcha_incorrecto")

    def test_credenciales_malas_no_se_marcan_reintentables(self):
        """La pantalla no debe invitar a reintentar: insistir con una clave que
        AFPnet acaba de rechazar puede bloquear el usuario de la empresa."""
        handle = self._pedir_desafio()
        with patch.object(
            client, "responder_desafio",
            side_effect=client.LoginRechazado("usuario incorrecto", captcha=False),
        ):
            respuesta = self.client.post(self.conectar_url, {
                "handle": handle, "usuario": "ADM0001",
                "clave": "mala", "captcha": "VCBL",
            }, format="json")

        self.assertFalse(respuesta.json()["reintentable"])
        self.assertEqual(respuesta.json()["code"], "credenciales")

    def test_cada_imagen_vale_para_un_solo_envio(self):
        handle = self._pedir_desafio()
        with patch.object(client, "responder_desafio", return_value={"s": "1"}):
            self.client.post(self.conectar_url, {
                "handle": handle, "usuario": "ADM0001",
                "clave": "secreta", "captcha": "VCBL",
            }, format="json")

            repetido = self.client.post(self.conectar_url, {
                "handle": handle, "usuario": "ADM0001",
                "clave": "secreta", "captcha": "VCBL",
            }, format="json")

        self.assertEqual(repetido.status_code, 409)
        self.assertEqual(repetido.json()["code"], "captcha_caducado")

    def test_un_handle_de_otra_empresa_no_sirve(self):
        """El desafío se guarda bajo la empresa: un handle filtrado no abre
        sesión desde otra cuenta."""
        handle = self._pedir_desafio()
        otro_usuario, otra_org = self.make_tenant("20100070970", "otro@empresa.pe")
        self.client.force_authenticate(otro_usuario)

        with patch.object(client, "responder_desafio", return_value={"s": "1"}):
            respuesta = self.client.post(self.conectar_url, {
                "handle": handle, "usuario": "ADM0001",
                "clave": "secreta", "captcha": "VCBL",
            }, format="json", HTTP_X_ORGANIZATION=otra_org.ruc)

        self.assertEqual(respuesta.status_code, 409)

    def test_faltan_campos(self):
        respuesta = self.client.post(self.conectar_url, {"handle": "x"},
                                     format="json")

        self.assertEqual(respuesta.status_code, 400)
        for campo in ("usuario", "clave", "captcha"):
            self.assertIn(campo, respuesta.json())

    def test_la_clave_no_queda_guardada(self):
        handle = self._pedir_desafio()
        with patch.object(client, "responder_desafio", return_value={"s": "1"}):
            self.client.post(self.conectar_url, {
                "handle": handle, "usuario": "ADM0001",
                "clave": "Pattern-de-prueba", "captcha": "VCBL",
            }, format="json")

        sesion = AfpnetSession.objects.get(organization=self.organization)
        volcado = str(sesion.__dict__)
        self.assertNotIn("Pattern-de-prueba", volcado)


class EstadoTests(TenantAPITestCase):
    RUC = "20604442533"

    def test_sin_conectar(self):
        respuesta = self.client.get(reverse("afpnet:estado"))

        self.assertEqual(respuesta.json()["estado"], "sin_conectar")

    def test_desconectar_borra_las_cookies(self):
        sesion = AfpnetSession.objects.create(
            organization=self.organization, taxpayer_id=self.RUC
        )
        sesion.marcar_activa({"s": "1"}, "ADM0001")

        self.client.post(reverse("afpnet:desconectar"))

        sesion.refresh_from_db()
        self.assertEqual(sesion.encrypted_cookies, "")
