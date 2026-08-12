"""El calendario de la aplicación es el de la empresa de quien llama.

El generador público sigue aceptando cualquier RUC por parámetro —para eso
nació—, pero la pantalla de dentro no puede depender de lo que el cliente
decida mandar. Aquí se comprueban las dos mitades:

* que el RUC, la planilla y el Buen Contribuyente salen de la membresía y de la
  ficha RUC, no del request;
* que la suscripción por token, que es la única ruta abierta, expone el
  cronograma y **nada más**.
"""

from __future__ import annotations

from datetime import date

from django.urls import reverse

from accounts.models import TaxRegime
from core.testing import TenantAPITestCase
from finance_analytics.models import AlertSeverity, AlertStatus, FinanceAlert
from ruc_profile.models import RucSnapshot

RUC = "20604442533"
OTRO_RUC = "20100070970"


def sembrar_ficha(ruc: str, *, trabajadores: int, registros: str = "NINGUNO",
                  tipo: str = "SOCIEDAD ANONIMA CERRADA", ok: bool = True):
    return RucSnapshot.objects.create(
        ruc=ruc,
        captured_on=date(2026, 8, 1),
        business_name=f"EMPRESA {ruc}",
        taxpayer_type=tipo,
        registries=registros,
        worker_count=trabajadores,
        latest_worker_period="2026-06",
        succeeded=ok,
    )


class ContextoDerivadoTests(TenantAPITestCase):
    RUC = RUC

    def setUp(self):
        self.url = reverse("sensor_sunat:calendario-mio")

    def test_la_planilla_sale_de_la_ficha_ruc_y_no_se_pregunta(self):
        sembrar_ficha(RUC, trabajadores=5)

        datos = self.client.get(self.url).json()

        self.assertTrue(datos["planilla"]["valor"])
        self.assertEqual(datos["planilla"]["origen"], "ficha")
        self.assertEqual(datos["trabajadores"], 5)
        self.assertIn("PLAME", datos["planilla"]["detalle"])

    def test_sin_trabajadores_desaparecen_los_vencimientos_laborales(self):
        sembrar_ficha(RUC, trabajadores=0)

        datos = self.client.get(self.url).json()

        self.assertFalse(datos["planilla"]["valor"])
        tipos = {e["tipo"] for e in datos["eventos"]}
        self.assertNotIn("LABORAL", tipos)
        self.assertNotIn("AFP", tipos)

    def test_una_ficha_fallida_no_borra_la_planilla(self):
        """Un scrapeo roto deja worker_count en cero. Tomarlo por bueno haría
        desaparecer la CTS y las gratificaciones de quien sí tiene planilla."""
        sembrar_ficha(RUC, trabajadores=0, ok=False)

        datos = self.client.get(self.url).json()

        self.assertTrue(datos["planilla"]["valor"])
        self.assertEqual(datos["planilla"]["origen"], "supuesto")

    def test_el_buen_contribuyente_sale_de_los_registros(self):
        sembrar_ficha(RUC, trabajadores=2, registros="REGIMEN BUEN CONTRIBUYENTE")

        datos = self.client.get(self.url).json()

        self.assertTrue(datos["buen_contribuyente"]["valor"])
        self.assertEqual(datos["buen_contribuyente"]["origen"], "ficha")

    def test_sin_regimen_declarado_se_asume_y_se_avisa(self):
        sembrar_ficha(RUC, trabajadores=1)

        datos = self.client.get(self.url).json()

        self.assertEqual(datos["regimen"], TaxRegime.RMT)
        self.assertFalse(datos["regimen_declarado"])
        self.assertTrue(any("Asumimos régimen" in a for a in datos["avisos"]))

    def test_una_sociedad_recibe_la_nota_sobre_el_rus(self):
        sembrar_ficha(RUC, trabajadores=1, tipo="SOCIEDAD ANONIMA CERRADA")

        datos = self.client.get(self.url).json()

        self.assertIn("Nuevo RUS no aplica", datos["nota_regimen"])


class DeclararRegimenTests(TenantAPITestCase):
    RUC = RUC

    def setUp(self):
        self.url = reverse("sensor_sunat:calendario-mio")

    def test_declarar_el_regimen_lo_guarda_en_la_empresa(self):
        respuesta = self.client.patch(self.url, {"regimen": "RER"}, format="json")

        self.assertEqual(respuesta.status_code, 200)
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.tax_regime, TaxRegime.RER)

    def test_el_rer_no_presenta_declaracion_jurada_anual(self):
        self.client.patch(self.url, {"regimen": "RER"}, format="json")

        datos = self.client.get(self.url).json()

        self.assertTrue(datos["regimen_declarado"])
        self.assertNotIn("DJ_ANUAL", {e["tipo"] for e in datos["eventos"]})

    def test_un_regimen_inventado_se_rechaza(self):
        respuesta = self.client.patch(self.url, {"regimen": "XYZ"}, format="json")

        self.assertEqual(respuesta.status_code, 400)
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.tax_regime, "")


class PanelTests(TenantAPITestCase):
    RUC = RUC

    def setUp(self):
        self.url = reverse("sensor_sunat:calendario-mio")
        sembrar_ficha(RUC, trabajadores=3)

    def test_las_alertas_son_solo_las_de_la_empresa(self):
        FinanceAlert.objects.create(
            account_ruc=RUC, dedup_key="a1", alert_type="detraccion",
            severity=AlertSeverity.CRITICAL, title="Detracción sin pagar",
            explanation="Falta el depósito.", period="202607",
        )
        FinanceAlert.objects.create(
            account_ruc=OTRO_RUC, dedup_key="a2", alert_type="detraccion",
            severity=AlertSeverity.CRITICAL, title="De otra empresa",
            explanation="No debe verse.", period="202607",
        )

        alertas = self.client.get(self.url).json()["alertas"]

        self.assertEqual([a["titulo"] for a in alertas], ["Detracción sin pagar"])

    def test_las_alertas_resueltas_no_aparecen(self):
        FinanceAlert.objects.create(
            account_ruc=RUC, dedup_key="a3", alert_type="detraccion",
            severity=AlertSeverity.HIGH, title="Ya resuelta",
            explanation="", period="202607", status=AlertStatus.RESOLVED,
        )

        self.assertEqual(self.client.get(self.url).json()["alertas"], [])

    def test_el_resumen_cuenta_alertas_prioritarias(self):
        FinanceAlert.objects.create(
            account_ruc=RUC, dedup_key="a4", alert_type="x",
            severity=AlertSeverity.CRITICAL, title="Urgente",
            explanation="", period="202607",
        )
        FinanceAlert.objects.create(
            account_ruc=RUC, dedup_key="a5", alert_type="x",
            severity=AlertSeverity.INFO, title="Informativa",
            explanation="", period="202607",
        )

        resumen = self.client.get(reverse("sensor_sunat:calendario-resumen")).json()

        self.assertEqual(resumen["alertas_prioritarias"], 1)
        self.assertIn(resumen["urgencia"], ("ninguna", "normal", "alta", "critica"))


class SuscripcionTests(TenantAPITestCase):
    RUC = RUC

    def setUp(self):
        sembrar_ficha(RUC, trabajadores=1)
        self.url = reverse("sensor_sunat:calendario-mio")

    def _token(self) -> str:
        self.organization.refresh_from_db()
        return self.organization.calendar_token

    def test_el_ics_por_token_no_necesita_sesion(self):
        ruta = reverse("sensor_sunat:calendario-suscripcion",
                       kwargs={"token": self._token()})
        self.client.force_authenticate(None)

        respuesta = self.client.get(ruta)

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("text/calendar", respuesta["Content-Type"])
        self.assertIn("BEGIN:VCALENDAR", respuesta.content.decode())

    def test_el_ics_no_filtra_alertas_ni_buzon(self):
        FinanceAlert.objects.create(
            account_ruc=RUC, dedup_key="secreta", alert_type="x",
            severity=AlertSeverity.CRITICAL, title="Detracción impaga secreta",
            explanation="No debe salir en el .ics.", period="202607",
        )
        ruta = reverse("sensor_sunat:calendario-suscripcion",
                       kwargs={"token": self._token()})
        self.client.force_authenticate(None)

        cuerpo = self.client.get(ruta).content.decode()

        self.assertNotIn("secreta", cuerpo.lower())

    def test_un_token_inventado_es_404(self):
        ruta = reverse("sensor_sunat:calendario-suscripcion",
                       kwargs={"token": "no-existe-este-token"})
        self.client.force_authenticate(None)

        self.assertEqual(self.client.get(ruta).status_code, 404)

    def test_rotar_invalida_la_url_anterior(self):
        anterior = self._token()
        ruta_anterior = reverse("sensor_sunat:calendario-suscripcion",
                                kwargs={"token": anterior})

        respuesta = self.client.post(
            reverse("sensor_sunat:calendario-suscripcion-rotar")
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertNotEqual(self._token(), anterior)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(ruta_anterior).status_code, 404)

    def test_la_respuesta_trae_las_tres_formas_de_suscribirse(self):
        suscripcion = self.client.get(self.url).json()["suscripcion"]

        self.assertTrue(suscripcion["webcal"].startswith("webcal://"))
        self.assertIn("calendar.google.com", suscripcion["google"])
        self.assertIn(self._token(), suscripcion["ics"])


class AislamientoTests(TenantAPITestCase):
    RUC = RUC

    def test_otra_empresa_ve_su_propio_cronograma(self):
        """El RUC sale de la membresía: mandar otro por parámetro no lo cambia."""
        sembrar_ficha(RUC, trabajadores=1)
        url = reverse("sensor_sunat:calendario-mio")

        datos = self.client.get(url, {"ruc": OTRO_RUC}).json()

        self.assertEqual(datos["ruc"], RUC)
