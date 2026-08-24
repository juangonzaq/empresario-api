"""La autorización de acceso a SUNAT: obligatoria, íntegra y demostrable."""

from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.models import SunatAuthorization
from accounts.services import consent
from accounts.tests.test_tenancy import KEY, make_org, make_user


@override_settings(FIELD_ENCRYPTION_KEY=KEY)
class AutorizacionTests(APITestCase):
    def setUp(self):
        from accounts.services import crypto
        crypto._fernet.cache_clear()
        self.ana = make_user("ana@uno.pe")
        self.org = make_org("20100000001", self.ana)
        self.client.force_authenticate(self.ana)

    def tearDown(self):
        from accounts.services import crypto
        crypto._fernet.cache_clear()

    def test_sin_aceptar_no_se_guarda_la_clave(self):
        r = self.client.post(reverse("accounts:sunat-connection"),
                             {"sol_username": "CONSULTA1", "sol_password": "x-clave"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("authorization_accepted", r.data)
        self.assertFalse(SunatAuthorization.objects.exists())

    def test_al_aceptar_queda_constancia_completa(self):
        r = self.client.post(reverse("accounts:sunat-connection"), {
            "sol_username": "CONSULTA1", "sol_password": "x-clave",
            "authorization_accepted": True, "authorization_version": consent.VERSION,
        }, format="json", HTTP_USER_AGENT="Safari iPhone", REMOTE_ADDR="190.1.2.3")
        self.assertEqual(r.status_code, 202, r.data)
        a = SunatAuthorization.objects.get()
        self.assertEqual(a.user, self.ana); self.assertEqual(a.sol_username, "CONSULTA1")
        self.assertEqual(a.version, consent.VERSION); self.assertEqual(a.text_sha256, consent.SHA256)
        self.assertEqual(a.ip_address, "190.1.2.3"); self.assertEqual(a.user_agent, "Safari iPhone")
        self.assertIn("buzon", a.scopes)
        d = self.client.get(reverse("accounts:sunat-authorization")).data
        self.assertEqual(d["current"]["version"], consent.VERSION)
        self.assertTrue(d["current"]["current_version"])
        self.assertEqual(d["document"]["sha256"], consent.SHA256)

    def test_version_vieja_se_rechaza(self):
        r = self.client.post(reverse("accounts:sunat-connection"), {
            "sol_username": "CONSULTA1", "sol_password": "x-clave",
            "authorization_accepted": True, "authorization_version": "0.1",
        }, format="json")
        self.assertEqual(r.status_code, 400); self.assertIn("authorization_version", r.data)

    def test_desconectar_la_marca_revocada_sin_borrarla(self):
        self.client.post(reverse("accounts:sunat-connection"), {
            "sol_username": "CONSULTA1", "sol_password": "x-clave", "authorization_accepted": True,
        }, format="json")
        self.client.delete(reverse("accounts:sunat-connection"))
        a = SunatAuthorization.objects.get(); self.assertIsNotNone(a.revoked_at); self.assertEqual(a.revoked_by, self.ana)
        self.assertIsNone(self.client.get(reverse("accounts:sunat-authorization")).data["current"])

    def test_el_texto_es_publico_y_lleva_huella(self):
        self.client.force_authenticate(None)
        r = self.client.get(reverse("accounts:consent-document"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["version"], consent.VERSION)
        self.assertTrue(len(r.data["parrafos"]) >= 5)
