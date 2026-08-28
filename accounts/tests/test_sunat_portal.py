"""Abrir el portal SOL con la sesión iniciada: quién puede y qué recibe."""

from __future__ import annotations

from unittest.mock import patch

import requests
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.models import Membership, Role, SunatConnectionStatus, SunatCredential
from accounts.services import sol_portal
from accounts.tests.test_tenancy import KEY, make_org, make_user


@override_settings(FIELD_ENCRYPTION_KEY=KEY)
class SunatPortalTests(APITestCase):
    def setUp(self):
        from accounts.services import crypto

        crypto._fernet.cache_clear()
        cache.clear()
        self.ana = make_user("ana@uno.pe")
        self.org = make_org("20100000001", self.ana)
        self.credential = SunatCredential.objects.create(
            organization=self.org, sol_username="CONSULTA1",
            status=SunatConnectionStatus.CONNECTED,
        )
        self.credential.set_password("mi-clave-sol")
        self.credential.save()
        self.client.force_authenticate(self.ana)
        self.url = reverse("accounts:sunat-portal")

    def tearDown(self):
        from accounts.services import crypto

        crypto._fernet.cache_clear()

    def test_owner_receives_the_login_form_as_sunat_expects_it(self):
        with patch.object(sol_portal, "fetch_menu_state", return_value="ESTADO-FRESCO"):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["action"], sol_portal.LOGIN_ACTION)
        self.assertEqual(response.data["method"], "POST")
        self.assertEqual(response.data["fields"], {
            "tipo": "2",
            "dni": "",
            "custom_ruc": "20100000001",
            "j_username": "CONSULTA1",
            "j_password": "mi-clave-sol",
            "captcha": "",
            "originalUrl": sol_portal.ORIGINAL_URL,
            "lang": "es-PE",
            "state": "ESTADO-FRESCO",
        })

    def test_state_is_cached_and_falls_back_when_sunat_is_down(self):
        with patch.object(
            sol_portal.requests, "get", side_effect=requests.ConnectionError("caída"),
        ):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["fields"]["state"], sol_portal.FALLBACK_STATE)

        with patch.object(sol_portal, "fetch_menu_state", return_value="NUEVO") as fetch:
            self.client.post(self.url)
            self.client.post(self.url)
        self.assertEqual(fetch.call_count, 1)

    def test_renta_destination_uses_erenta_oauth_login(self):
        class Redireccion:
            status_code = 302
            headers = {"Location": (
                "https://api-seguridad.sunat.gob.pe/v1/clientessol/03590141-x/oauth2/login"
                "?originalUrl=https://e-renta.sunat.gob.pe/loader/x&state=null"
            )}

        with patch.object(sol_portal.requests, "get", return_value=Redireccion()) as get:
            response = self.client.post(self.url, {"destino": "renta"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get.call_args.args[0], sol_portal.RENTA_AUTHEN_URL)
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertEqual(
            response.data["action"],
            "https://api-seguridad.sunat.gob.pe/v1/clientessol/03590141-x/oauth2/j_security_check",
        )
        self.assertEqual(response.data["fields"]["originalUrl"], "https://e-renta.sunat.gob.pe/loader/x")
        self.assertEqual(response.data["fields"]["state"], "null")
        self.assertEqual(response.data["fields"]["j_password"], "mi-clave-sol")

    def test_renta_when_sunat_is_down_is_a_503_without_the_key(self):
        with patch.object(
            sol_portal.requests, "get", side_effect=requests.ConnectionError("caída"),
        ):
            response = self.client.post(self.url, {"destino": "renta"}, format="json")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["code"], "portal")
        self.assertNotIn("mi-clave-sol", str(response.data))

    def test_declaraciones_lands_on_the_classic_menu(self):
        with patch.object(sol_portal, "fetch_menu_state", return_value="X"):
            response = self.client.post(self.url, {"destino": "declaraciones"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["action"], sol_portal.LOGIN_ACTION)

    def test_unknown_destination_is_rejected(self):
        response = self.client.post(self.url, {"destino": "loteria"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_sunafil_portal_form(self):
        from accounts.services import sunafil_portal

        entry_html = (
            'var pathurl = "https://api-seguridad.sunat.gob.pe"; var cid = "b647-x";'
            ' var st = "s"; var redirectURL = "https://casillaelectronica.sunafil.gob.pe/x";'
        )

        class Entrada:
            status_code = 200
            text = entry_html
            def raise_for_status(self): pass

        class Redireccion:
            status_code = 302
            headers = {"Location": (
                "https://api-seguridad.sunat.gob.pe/v1/clientessol/b647-x/oauth2/login"
                "?originalUrl=https://casillaelectronica.sunafil.gob.pe/si.inbox/Login/Empresa&state=s"
            )}

        def fake_get(url, **kwargs):
            return Entrada() if url == sunafil_portal.ENTRY_URL else Redireccion()

        with patch("sunafil.services.client.requests.Session.get", side_effect=fake_get):
            response = self.client.post(reverse("accounts:sunafil-portal"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["action"],
            "https://api-seguridad.sunat.gob.pe/v1/clientessol/b647-x/oauth2/j_security_check",
        )
        self.assertEqual(response.data["fields"]["custom_ruc"], "20100000001")
        self.assertEqual(response.data["fields"]["state"], "s")

    def test_sunafil_portal_needs_credentials_and_role(self):
        lectura = make_user("lectura2@uno.pe")
        Membership.objects.create(user=lectura, organization=self.org, role=Role.VIEWER)
        self.client.force_authenticate(lectura)
        self.assertEqual(self.client.post(reverse("accounts:sunafil-portal")).status_code, 403)

        self.client.force_authenticate(self.ana)
        self.credential.delete()
        response = self.client.post(reverse("accounts:sunafil-portal"))
        self.assertEqual(response.status_code, 409)

    def test_get_is_not_allowed(self):
        # El formulario lleva la clave: no debe poder pedirse con un GET.
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_viewer_cannot_take_the_key(self):
        lectura = make_user("lectura@uno.pe")
        Membership.objects.create(user=lectura, organization=self.org, role=Role.VIEWER)
        self.client.force_authenticate(lectura)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("mi-clave-sol", str(response.data))

    def test_accountant_can(self):
        contador = make_user("contador@uno.pe")
        Membership.objects.create(
            user=contador, organization=self.org, role=Role.ACCOUNTANT,
        )
        self.client.force_authenticate(contador)
        with patch.object(sol_portal, "fetch_menu_state", return_value="X"):
            self.assertEqual(self.client.post(self.url).status_code, 200)

    def test_another_company_never_sees_this_key(self):
        beto = make_user("beto@dos.pe")
        make_org("20200000002", beto)
        self.client.force_authenticate(beto)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "sin_conectar")
        self.assertNotIn("mi-clave-sol", str(response.data))

    def test_without_credentials_it_says_to_connect_first(self):
        self.credential.delete()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "sin_conectar")

    def test_rejected_credentials_are_not_resent(self):
        self.credential.status = SunatConnectionStatus.INVALID
        self.credential.save()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "invalida")
        self.assertNotIn("mi-clave-sol", str(response.data))

    def test_anonymous_is_rejected(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(self.url).status_code, 401)


class MenuStateParsingTests(APITestCase):
    def test_state_is_read_from_the_menu_redirect(self):
        class Respuesta:
            text = (
                'redirect("https://api-seguridad.sunat.gob.pe/v1/clientessol/x/oauth2/'
                'authen?redirect_uri=https://e-menu.sunat.gob.pe/cl-ti-itmenu/'
                'AutenticaMenuInternet.htm&state=rO0ABc+/Zm9v==&client_id=x'
                '&response_type=code");'
            )

            def raise_for_status(self):
                pass

        with patch.object(sol_portal.requests, "get", return_value=Respuesta()):
            self.assertEqual(sol_portal.fetch_menu_state(), "rO0ABc+/Zm9v==")
