"""Que ninguna empresa vea los datos de otra, comprobado contra datos reales.

Se siembran filas de dos empresas en cada app y se consulta el API como cada
una. La afirmación no es «devuelve algo», es «no devuelve lo ajeno»: por eso
cada caso comprueba tanto lo propio como la ausencia de lo del vecino.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Membership, Organization, Role, User
from compliance_profile.models import ComplianceRating
from sunafil.models import SunafilItem
from sunat_cpe.models import Direction, DocumentClass, ElectronicInvoice
from sunat_itf.models import ItfRecord
from sunat_mailbox.models import Message

UNO = "20100000001"
DOS = "20200000002"


class CrossTenantLeakTests(APITestCase):
    """Una fila por empresa en cada app, y se comprueba quién ve qué."""

    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user(
            email="ana@uno.pe", password="una-clave-larga-99",
            email_verified_at=timezone.now(),
        )
        cls.beto = User.objects.create_user(
            email="beto@dos.pe", password="una-clave-larga-99",
            email_verified_at=timezone.now(),
        )
        for user, ruc in ((cls.ana, UNO), (cls.beto, DOS)):
            org = Organization.objects.create(ruc=ruc, name=f"EMPRESA {ruc}")
            Membership.objects.create(user=user, organization=org, role=Role.OWNER)

        for i, ruc in enumerate((UNO, DOS), start=1):
            Message.objects.create(
                taxpayer_id=ruc, message_code=i, message_type=1,
                subject=f"Aviso de {ruc}", sent_on=date(2026, 7, 1),
            )
            ItfRecord.objects.create(
                taxpayer_id=ruc, section="accumulated", period="202607",
                declarant_name="BANCO", operation_code="12",
                base_amount=Decimal("1000"), tax=Decimal("0.05"),
            )
            ElectronicInvoice.objects.create(
                account_ruc=ruc, direction=Direction.ISSUED,
                document_class=DocumentClass.INVOICE, document_type="10",
                issuer_ruc=ruc, series="E001", number=str(i),
                full_number=f"E001-{i}", period="202607", currency="PEN",
                total_amount=Decimal("1000"),
            )
            SunafilItem.objects.create(
                taxpayer_id=ruc, kind="orientacion", external_key=f"S{i}",
                subject=f"Casilla de {ruc}",
                first_seen_on=date(2026, 7, 1), last_seen_on=date(2026, 7, 1),
            )
            ComplianceRating.objects.create(
                taxpayer_id=ruc, period=20261, execution_period=20261,
                rating="A", is_current=True,
            )

    def _ids(self, url: str) -> list[dict]:
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, f"{url} → {response.status_code}")
        data = response.data
        return data["results"] if isinstance(data, dict) and "results" in data else data

    def test_each_company_sees_only_its_own_rows(self):
        cases = [
            ("/api/messages/", "taxpayer_id"),
            ("/api/itf/records/", "taxpayer_id"),
            ("/api/cpe/invoices/", "account_ruc"),
            ("/api/sunafil/", "taxpayer_id"),
            ("/api/compliance/ratings/", "taxpayer_id"),
        ]
        for user, own, other in ((self.ana, UNO, DOS), (self.beto, DOS, UNO)):
            self.client.force_authenticate(user)
            for url, field in cases:
                rows = self._ids(url)
                self.assertTrue(rows, f"{url} no devolvió nada para {own}")
                rucs = {r.get(field) for r in rows}
                self.assertEqual(
                    rucs, {own},
                    f"{url} le mostró a {own} filas de {rucs - {own}}",
                )
                self.assertNotIn(other, rucs)

    def test_a_query_parameter_cannot_widen_the_scope(self):
        """El truco clásico: pedir ?taxpayer_id= de otra empresa."""
        self.client.force_authenticate(self.ana)
        for url in ("/api/messages/", "/api/itf/records/", "/api/sunafil/"):
            rows = self._ids(f"{url}?taxpayer_id={DOS}")
            self.assertEqual(
                [r for r in rows if r.get("taxpayer_id") == DOS], [],
                f"{url} filtró datos de otra empresa vía query param",
            )

    def test_detail_of_another_companys_row_is_404(self):
        ajeno = Message.objects.get(taxpayer_id=DOS)
        self.client.force_authenticate(self.ana)
        response = self.client.get(f"/api/messages/{ajeno.id}/")
        self.assertEqual(response.status_code, 404)

    def test_everything_is_closed_to_anonymous_callers(self):
        self.client.force_authenticate(None)
        for url in (
            "/api/messages/", "/api/itf/records/", "/api/cpe/invoices/",
            "/api/sunafil/", "/api/compliance/ratings/",
            "/api/finance/overview/", "/api/sync/status/",
        ):
            self.assertEqual(
                self.client.get(url).status_code, 401,
                f"{url} respondió sin bearer",
            )

    def test_el_resumen_de_cumplimiento_no_cruza_empresas(self):
        """`/api/compliance/summary/` partía de `objects.all()` y solo acotaba
        si el cliente mandaba `?taxpayer_id=`: devolvía la calificación más
        reciente de cualquiera, y el parámetro dejaba elegir de quién."""
        for user, own, other in ((self.ana, UNO, DOS), (self.beto, DOS, UNO)):
            self.client.force_authenticate(user)
            for url in ("/api/compliance/summary/", "/api/compliance/findings/"):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, f"{url} → {own}")
                self.assertNotIn(
                    other, str(response.data),
                    f"{url} le mostró a {own} datos de {other}",
                )
                # Y el parámetro no puede ampliar el alcance.
                ajeno = self.client.get(f"{url}?taxpayer_id={other}")
                self.assertNotIn(
                    other, str(ajeno.data),
                    f"{url} filtró datos de {other} vía query param",
                )


class EmpresaRecienRegistradaTests(APITestCase):
    """Una empresa sin datos propios debe ver vacío, no lo del vecino.

    Es el caso que destapó la fuga: al registrar un RUC nuevo, el perfil de
    cumplimiento aparecía relleno con el de otra cuenta.
    """

    @classmethod
    def setUpTestData(cls):
        cls.nueva = User.objects.create_user(
            email="nueva@tres.pe", password="una-clave-larga-99",
            email_verified_at=timezone.now(),
        )
        org = Organization.objects.create(ruc="20300000003", name="EMPRESA NUEVA")
        Membership.objects.create(user=cls.nueva, organization=org, role=Role.OWNER)

        # La otra empresa sí tiene cumplimiento cargado.
        ComplianceRating.objects.create(
            taxpayer_id=DOS, period=20261, execution_period=20261,
            rating="A", is_current=True,
        )

    def test_sin_datos_propios_el_cumplimiento_sale_vacio(self):
        self.client.force_authenticate(self.nueva)
        for url in ("/api/compliance/summary/", "/api/compliance/findings/"):
            response = self.client.get(url)
            self.assertEqual(
                response.status_code, 404,
                f"{url} devolvió datos a una empresa que no tiene ninguno",
            )


class CambioDeEmpresaTests(APITestCase):
    """Un contador con dos clientes: lo que ve depende de cuál tenga elegido.

    Es el contrato del que cuelga el selector de empresa de la barra superior.
    La empresa no viaja en la URL de cada pantalla sino en una cabecera, así
    que si esa cabecera no mandara de verdad, cambiar de empresa movería el
    nombre de arriba y dejaría debajo las cifras de la anterior — que es la
    manera más silenciosa posible de que alguien decida sobre la empresa
    equivocada.
    """

    @classmethod
    def setUpTestData(cls):
        cls.contador = User.objects.create_user(
            email="contador@estudio.pe", password="una-clave-larga-99",
            email_verified_at=timezone.now(),
        )
        for ruc in (UNO, DOS):
            org = Organization.objects.create(ruc=ruc, name=f"EMPRESA {ruc}")
            Membership.objects.create(
                user=cls.contador, organization=org, role=Role.ACCOUNTANT
            )
            Message.objects.create(
                taxpayer_id=ruc, message_code=1, message_type=1,
                subject=f"Aviso de {ruc}", sent_on=date(2026, 7, 1),
            )
        ComplianceRating.objects.create(
            taxpayer_id=UNO, period=20261, execution_period=20261,
            rating="A", is_current=True,
        )
        ComplianceRating.objects.create(
            taxpayer_id=DOS, period=20261, execution_period=20261,
            rating="E", is_current=True,
        )

    def setUp(self):
        self.client.force_authenticate(self.contador)

    def _asuntos(self, ruc: str) -> set[str]:
        response = self.client.get("/api/messages/", HTTP_X_ORGANIZATION=ruc)
        self.assertEqual(response.status_code, 200)
        datos = response.data
        filas = datos["results"] if isinstance(datos, dict) else datos
        return {fila["subject"] for fila in filas}

    def test_la_cabecera_decide_de_quien_son_los_datos(self):
        self.assertEqual(self._asuntos(UNO), {f"Aviso de {UNO}"})
        self.assertEqual(self._asuntos(DOS), {f"Aviso de {DOS}"})

    def test_el_cumplimiento_tambien_cambia(self):
        """No solo los listados: también lo que se calcula por empresa."""
        for ruc, nota in ((UNO, "A"), (DOS, "E")):
            response = self.client.get(
                "/api/compliance/summary/", HTTP_X_ORGANIZATION=ruc
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["taxpayer_id"], ruc)
            self.assertEqual(response.data["rating"], nota)

    def test_con_varias_empresas_hay_que_decir_cual(self):
        """Sin cabecera no se adivina: elegir por él podría enseñarle datos de
        una empresa mientras cree estar mirando la otra."""
        self.assertEqual(self.client.get("/api/messages/").status_code, 404)

    def test_no_se_puede_elegir_una_empresa_ajena(self):
        ajena = Organization.objects.create(ruc="20900000009", name="AJENA")
        Message.objects.create(
            taxpayer_id=ajena.ruc, message_code=9, message_type=1,
            subject="Aviso ajeno", sent_on=date(2026, 7, 1),
        )
        response = self.client.get(
            "/api/messages/", HTTP_X_ORGANIZATION=ajena.ruc
        )
        self.assertEqual(response.status_code, 404)
