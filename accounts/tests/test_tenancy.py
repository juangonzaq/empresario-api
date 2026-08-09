"""Aislamiento entre empresas: lo que una ve y, sobre todo, lo que no.

Estos son los tests que importan en un servicio multiempresa. Un fallo aquí no
es un bug de interfaz: es la contabilidad de un cliente en la pantalla de otro.
"""

from __future__ import annotations

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import (
    Membership, Organization, Role, SunatConnectionStatus, SunatCredential, User,
)
from accounts.services.crypto import decrypt

PASSWORD = "una-clave-larga-99"
KEY = "D__dbkJT6pvD6tQBqyl37GUdmyuBp3ZPYM220eV9y6Q="   # llave Fernet de prueba


def make_user(email: str) -> User:
    return User.objects.create_user(
        email=email, password=PASSWORD, email_verified_at=timezone.now()
    )


def make_org(ruc: str, user: User, role: str = Role.OWNER) -> Organization:
    org = Organization.objects.create(ruc=ruc, name=f"EMPRESA {ruc}")
    Membership.objects.create(user=user, organization=org, role=role)
    return org


class TenantResolutionTests(APITestCase):
    def setUp(self):
        self.ana = make_user("ana@uno.pe")
        self.beto = make_user("beto@dos.pe")
        self.uno = make_org("20100000001", self.ana)
        self.dos = make_org("20200000002", self.beto)

    def test_user_only_sees_their_own_organizations(self):
        self.client.force_authenticate(self.ana)
        data = self.client.get(reverse("accounts:organizations")).data
        self.assertEqual([o["ruc"] for o in data], ["20100000001"])

    def test_sync_status_is_scoped_to_the_caller(self):
        self.client.force_authenticate(self.ana)
        response = self.client.get(reverse("sync:status"))
        self.assertEqual(response.status_code, 200)

    def test_asking_for_someone_elses_organization_is_404(self):
        self.client.force_authenticate(self.ana)
        response = self.client.get(
            reverse("sync:status"), HTTP_X_ORGANIZATION=self.dos.ruc
        )
        # Ni 403 ni datos: 404, para no confirmar siquiera que ese RUC existe.
        self.assertEqual(response.status_code, 404)

    def test_user_without_organizations_gets_a_clear_conflict(self):
        solo = make_user("sola@nadie.pe")
        self.client.force_authenticate(solo)
        response = self.client.get(reverse("sync:status"))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "sin_organizacion")

    def test_with_several_organizations_the_header_decides(self):
        Membership.objects.create(
            user=self.ana, organization=self.dos, role=Role.ACCOUNTANT
        )
        self.client.force_authenticate(self.ana)
        # Sin cabecera y con dos empresas, hay que elegir.
        self.assertEqual(self.client.get(reverse("sync:status")).status_code, 404)
        ok = self.client.get(reverse("sync:status"), HTTP_X_ORGANIZATION=self.dos.ruc)
        self.assertEqual(ok.status_code, 200)

    def test_anonymous_is_rejected(self):
        self.assertEqual(self.client.get(reverse("sync:status")).status_code, 401)

    def test_ruc_already_registered_cannot_be_claimed_by_another_user(self):
        self.client.force_authenticate(self.beto)
        response = self.client.post(
            reverse("accounts:organizations"), {"ruc": self.uno.ruc}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Organization.objects.filter(ruc=self.uno.ruc).count(), 1)

    def test_unverified_email_cannot_register_a_company(self):
        nuevo = User.objects.create_user(email="sin@verificar.pe", password=PASSWORD)
        self.client.force_authenticate(nuevo)
        response = self.client.post(
            reverse("accounts:organizations"), {"ruc": "20300000003"}, format="json"
        )
        self.assertEqual(response.status_code, 403)


class RoleTests(APITestCase):
    def setUp(self):
        self.owner = make_user("titular@uno.pe")
        self.viewer = make_user("lectura@uno.pe")
        self.org = make_org("20100000001", self.owner)
        Membership.objects.create(
            user=self.viewer, organization=self.org, role=Role.VIEWER
        )

    def test_viewer_can_read_but_not_launch_a_sync(self):
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.get(reverse("sync:status")).status_code, 200)
        self.assertEqual(self.client.post(reverse("sync:start")).status_code, 403)


@override_settings(FIELD_ENCRYPTION_KEY=KEY)
class SunatCredentialTests(APITestCase):
    def setUp(self):
        from accounts.services import crypto

        crypto._fernet.cache_clear()
        self.ana = make_user("ana@uno.pe")
        self.org = make_org("20100000001", self.ana)
        self.client.force_authenticate(self.ana)

    def tearDown(self):
        from accounts.services import crypto

        crypto._fernet.cache_clear()

    def test_password_is_stored_encrypted_and_never_returned(self):
        response = self.client.post(
            reverse("accounts:sunat-connection"),
            {"sol_username": "CONSULTA1", "sol_password": "mi-clave-sol"},
            format="json",
        )
        self.assertEqual(response.status_code, 202)
        body = str(response.data)
        self.assertNotIn("mi-clave-sol", body)

        credential = SunatCredential.objects.get()
        # En la base está cifrada, no en claro.
        self.assertNotEqual(credential.encrypted_password, "mi-clave-sol")
        self.assertNotIn("mi-clave-sol", credential.encrypted_password)
        # Y se puede recuperar para dársela a SUNAT.
        self.assertEqual(decrypt(credential.encrypted_password), "mi-clave-sol")
        self.assertEqual(credential.status, SunatConnectionStatus.PENDING)

    def test_get_never_exposes_the_password(self):
        self.client.post(
            reverse("accounts:sunat-connection"),
            {"sol_username": "CONSULTA1", "sol_password": "mi-clave-sol"},
            format="json",
        )
        response = self.client.get(reverse("accounts:sunat-connection"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("password", str(response.data))
        self.assertNotIn("mi-clave-sol", str(response.data))

    def test_primary_user_is_recorded_and_warned_about(self):
        response = self.client.post(
            reverse("accounts:sunat-connection"),
            {"sol_username": "PRINCIPAL", "sol_password": "x-clave",
             "is_primary_user": True},
            format="json",
        )
        self.assertTrue(SunatCredential.objects.get().uses_primary_user)
        self.assertIn("secundario", response.data["warning"])

    def test_connecting_enqueues_a_sync_job(self):
        response = self.client.post(
            reverse("accounts:sunat-connection"),
            {"sol_username": "CONSULTA1", "sol_password": "x-clave"},
            format="json",
        )
        self.assertIn("sync_job", response.data)
        from sync.models import SyncJob

        job = SyncJob.objects.get()
        self.assertEqual(job.organization, self.org)
        self.assertGreater(job.total_steps, 0)

    def test_another_company_cannot_read_the_connection(self):
        self.client.post(
            reverse("accounts:sunat-connection"),
            {"sol_username": "CONSULTA1", "sol_password": "x-clave"}, format="json",
        )
        beto = make_user("beto@dos.pe")
        make_org("20200000002", beto)
        self.client.force_authenticate(beto)
        response = self.client.get(reverse("accounts:sunat-connection"))
        self.assertEqual(response.data["status"], "sin_conectar")

    def test_disconnect_removes_the_credential(self):
        self.client.post(
            reverse("accounts:sunat-connection"),
            {"sol_username": "CONSULTA1", "sol_password": "x-clave"}, format="json",
        )
        self.assertEqual(
            self.client.delete(reverse("accounts:sunat-connection")).status_code, 204
        )
        self.assertFalse(SunatCredential.objects.exists())
