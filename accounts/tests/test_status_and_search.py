"""La barra superior deja de inventarse el estado de la empresa.

Los avisos estaban escritos en el código del frontend: «Condición: Habido»,
«REMYPE acreditado». Se pintaban iguales para cualquier empresa, incluida una
recién registrada de la que no se había consultado nada. Lo que se fija aquí es
la regla que lo sustituye: **sin dato no se afirma nada**, y ``desconocido`` es
un estado de primera clase, distinto de «está bien».

El buscador tampoco existía. Al ser global es el sitio más fácil para colar
datos de otra empresa sin darse cuenta, así que se comprueba explícitamente.
"""

from __future__ import annotations

from datetime import date

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Membership, Organization, Role, User
from core.testing import DEFAULT_RUC, TenantAPITestCase
from remype.models import RemypeCheck
from ruc_profile.models import RucSnapshot
from suppliers.models import Supplier
from sunat_mailbox.models import Message

OTRA = "20200000002"


class EstadoDeLaEmpresaTests(TenantAPITestCase):
    def setUp(self):
        self.url = reverse("accounts:company-status")

    def avisos(self) -> dict[str, dict]:
        respuesta = self.client.get(self.url).data
        return {a["etiqueta"]: a for a in respuesta["avisos"]}

    def test_sin_datos_no_afirma_nada(self):
        estados = {a["estado"] for a in self.client.get(self.url).data["avisos"]}
        self.assertEqual(estados, {"desconocido"})

    def test_condicion_habido_y_activo_sale_en_verde(self):
        RucSnapshot.objects.create(
            ruc=DEFAULT_RUC, captured_on=date(2026, 8, 1), succeeded=True,
            status="ACTIVO", condition="HABIDO",
        )
        aviso = next(
            a for a in self.client.get(self.url).data["avisos"]
            if "Condición" in a["etiqueta"]
        )
        self.assertEqual(aviso["estado"], "ok")

    def test_cualquier_condicion_distinta_de_habido_avisa(self):
        """Los valores que SUNAT invente mañana deben caer del lado del aviso,
        no del visto bueno."""
        RucSnapshot.objects.create(
            ruc=DEFAULT_RUC, captured_on=date(2026, 8, 1), succeeded=True,
            status="ACTIVO", condition="NO HALLADO",
        )
        aviso = next(
            a for a in self.client.get(self.url).data["avisos"]
            if "Condición" in a["etiqueta"]
        )
        self.assertEqual(aviso["estado"], "atencion")

    def test_remype_no_acreditado_avisa(self):
        RemypeCheck.objects.create(
            ruc=DEFAULT_RUC, checked_on=date(2026, 8, 1), succeeded=True,
            is_registered=False, changed=False, payload={},
        )
        etiquetas = self.avisos()
        self.assertIn("Sin acreditación REMYPE", etiquetas)
        self.assertEqual(etiquetas["Sin acreditación REMYPE"]["estado"], "atencion")

    def test_el_buzon_cuenta_los_urgentes_sin_revisar(self):
        Message.objects.create(
            taxpayer_id=DEFAULT_RUC, message_code=1, message_type=1,
            subject="Requerimiento", sent_on=date(2026, 8, 1), is_urgent=True,
        )
        aviso = next(
            a for a in self.client.get(self.url).data["avisos"]
            if "urgentes" in a["etiqueta"]
        )
        self.assertEqual(aviso["estado"], "atencion")
        self.assertIn("1 mensajes urgentes", aviso["etiqueta"])

    def test_no_mira_los_datos_de_otra_empresa(self):
        RucSnapshot.objects.create(
            ruc=OTRA, captured_on=date(2026, 8, 1), succeeded=True,
            status="ACTIVO", condition="HABIDO",
        )
        estados = {a["estado"] for a in self.client.get(self.url).data["avisos"]}
        self.assertEqual(estados, {"desconocido"})


class BuscadorGlobalTests(TenantAPITestCase):
    def setUp(self):
        self.url = reverse("accounts:global-search")
        Supplier.objects.create(
            account_ruc=DEFAULT_RUC, ruc="20100070970", alias="Imprenta del jirón"
        )
        Message.objects.create(
            taxpayer_id=DEFAULT_RUC, message_code=7, message_type=1,
            subject="Aviso de imprenta", sent_on=date(2026, 8, 1),
        )

    def grupos(self, q: str) -> dict[str, dict]:
        respuesta = self.client.get(f"{self.url}?q={q}").data
        return {g["tipo"]: g for g in respuesta["grupos"]}

    def test_encuentra_proveedores_y_mensajes(self):
        grupos = self.grupos("imprenta")
        self.assertEqual(grupos["proveedores"]["total"], 1)
        self.assertEqual(
            grupos["proveedores"]["resultados"][0]["titulo"], "Imprenta del jirón"
        )
        self.assertEqual(grupos["mensajes"]["total"], 1)

    def test_busca_tambien_por_ruc(self):
        self.assertEqual(self.grupos("20100070970")["proveedores"]["total"], 1)

    def test_una_sola_letra_no_dispara_la_busqueda(self):
        """Con un carácter la consulta recorre todo sin acotar nada."""
        self.assertEqual(self.client.get(f"{self.url}?q=i").data["grupos"], [])

    def test_los_grupos_vacios_no_se_devuelven(self):
        self.assertNotIn("comprobantes", self.grupos("imprenta"))


class BuscadorNoCruzaEmpresasTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ajena = User.objects.create_user(
            email="ajena@dos.pe", password="una-clave-larga-99",
            email_verified_at=timezone.now(),
        )
        org = Organization.objects.create(ruc=OTRA, name="OTRA EMPRESA")
        Membership.objects.create(user=cls.ajena, organization=org, role=Role.OWNER)

        Supplier.objects.create(
            account_ruc=DEFAULT_RUC, ruc="20100070970", alias="Imprenta del jirón"
        )
        Message.objects.create(
            taxpayer_id=DEFAULT_RUC, message_code=7, message_type=1,
            subject="Aviso de imprenta", sent_on=date(2026, 8, 1),
        )

    def test_una_empresa_no_encuentra_lo_de_otra(self):
        self.client.force_authenticate(self.ajena)
        url = reverse("accounts:global-search")
        self.assertEqual(self.client.get(f"{url}?q=imprenta").data["grupos"], [])
