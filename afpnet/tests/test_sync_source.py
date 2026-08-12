"""AFPnet dentro del trabajo de sincronización.

La particularidad de esta fuente es que no puede abrir sesión sola. Lo que se
comprueba aquí es que eso se note de la manera correcta:

* sin sesión, el paso **falla con un motivo accionable** en lugar de quedarse
  verde sin haber traído nada;
* que AFPnet necesite un CAPTCHA **no invalida la credencial SOL** ni corta el
  resto del trabajo — son portales distintos.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from accounts.models import Organization, SunatConnectionStatus, SunatCredential
from afpnet.models import AfpnetSession, SessionStatus
from afpnet.services import client, portal
from afpnet.services.sync import SinSesion, sincronizar
from sync.models import JobKind, StepStatus, SyncJob
from sync.services import execute_step
from sync.sources import SOURCES_BY_KEY, initial_steps

RUC = "20604442533"


class FuenteRegistradaTests(TestCase):
    def test_afpnet_no_depende_de_la_clave_sol(self):
        """Si dependiera, un login SOL fallido la marcaría «omitida» — y AFPnet
        no tiene nada que ver con SUNAT."""
        fuente = SOURCES_BY_KEY["afpnet"]

        self.assertFalse(fuente.needs_sol)

    def test_corre_al_conectar_y_una_vez_al_mes(self):
        fuente = SOURCES_BY_KEY["afpnet"]

        self.assertTrue(fuente.runs_on(JobKind.INITIAL))
        self.assertTrue(fuente.runs_on(JobKind.MONTHLY))
        self.assertFalse(fuente.runs_on(JobKind.DAILY))


class SincronizarTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(ruc=RUC, name="PATTERN")

    def test_sin_sesion_se_pide_conectar(self):
        with self.assertRaises(SinSesion) as ctx:
            sincronizar(self.organization)

        self.assertIn("CAPTCHA", str(ctx.exception))

    def test_una_sesion_caducada_no_se_da_por_buena(self):
        sesion = AfpnetSession.objects.create(
            organization=self.organization, taxpayer_id=RUC
        )
        sesion.marcar_activa({"c": "1"}, "ADM0001")
        # El portal responde con la pantalla de login: la sesión murió.
        with patch.object(
            client, "comprobar_sesion",
            side_effect=client.SesionCaducada("caducó"),
        ):
            with self.assertRaises(SinSesion):
                sincronizar(self.organization)

        sesion.refresh_from_db()
        self.assertEqual(sesion.status, SessionStatus.EXPIRED)
        self.assertEqual(sesion.encrypted_cookies, "")

    def _con_sesion(self):
        sesion = AfpnetSession.objects.create(
            organization=self.organization, taxpayer_id=RUC
        )
        sesion.marcar_activa({"c": "1"}, "ADM0001")
        return sesion

    def test_el_portal_cerrado_no_caduca_la_sesion(self):
        """AFPnet cierra por las noches. Confundirlo con una sesión muerta
        borraría cookies que mañana habrían servido, y obligaría a resolver un
        CAPTCHA de más."""
        sesion = self._con_sesion()

        with patch.object(portal, "Portal",
                          side_effect=client.PortalCerrado("AFPnet está cerrado")):
            with self.assertRaises(client.PortalCerrado):
                sincronizar(self.organization)

        sesion.refresh_from_db()
        self.assertEqual(sesion.status, SessionStatus.ACTIVE)
        self.assertTrue(sesion.is_usable)

    def test_la_sesion_caducada_a_media_sincronizacion_se_marca(self):
        sesion = self._con_sesion()

        with patch.object(portal, "Portal",
                          side_effect=client.SesionCaducada("caducó")):
            with self.assertRaises(SinSesion):
                sincronizar(self.organization)

        sesion.refresh_from_db()
        self.assertEqual(sesion.status, SessionStatus.EXPIRED)
        self.assertEqual(sesion.encrypted_cookies, "")

    def test_guarda_lo_que_trae_el_portal(self):
        """La prueba de que la cadena entera escribe en la base: portal →
        parsers → modelos."""
        from decimal import Decimal

        from afpnet.models import AfpnetCompany, AfpnetDeclaration
        from afpnet.services import parsers

        self._con_sesion()

        falso = MagicMock()
        falso.datos_empresa.return_value = parsers.DatosEmpresa(
            ruc=RUC, razon_social="PATTERN GROUP S.A.C",
            representante="MARIA PEREZ", representante_correo="c@ejemplo.pe",
        )
        falso.planillas.return_value = [parsers.Planilla(
            periodo="202607", afp="habitat", numero="2610640231",
            nominal_fondo=Decimal("1370.00"), nominal_ryr=Decimal("187.69"),
            estado="PAGADA", tipo_trabajador="DEPENDIENTE", banco="BCP",
        )]
        falso.resumen_situacion.return_value = [
            parsers.ResumenDevengue(periodo="202607", total_op=5, op_presunta=5)
        ]
        falso.deudas.return_value = parsers.ReporteDeuda(ruc=RUC)

        with patch.object(portal, "Portal", return_value=falso):
            resultado = sincronizar(self.organization, desde_anio=2026,
                                    hoy=date(2026, 7, 15))

        self.assertEqual(resultado["planillas"], 1)
        self.assertEqual(resultado["periodos"], 1)
        planilla = AfpnetDeclaration.objects.get(taxpayer_id=RUC)
        self.assertEqual(planilla.nominal_fondo, Decimal("1370.00"))
        self.assertTrue(planilla.is_paid)
        self.assertEqual(planilla.bank, "BCP")
        empresa = AfpnetCompany.objects.get(organization=self.organization)
        self.assertEqual(empresa.representative, "MARIA PEREZ")

    def test_volver_a_sincronizar_actualiza_en_vez_de_duplicar(self):
        """Una planilla pasa de PENDIENTE a PAGADA días después, y es la misma
        planilla."""
        from afpnet.models import AfpnetDeclaration
        from afpnet.services import parsers

        self._con_sesion()

        def portal_con(estado):
            falso = MagicMock()
            falso.datos_empresa.return_value = parsers.DatosEmpresa(ruc=RUC)
            falso.planillas.return_value = [parsers.Planilla(
                periodo="202607", afp="habitat", numero="2610640231",
                estado=estado,
            )]
            falso.resumen_situacion.return_value = []
            falso.deudas.return_value = parsers.ReporteDeuda(ruc=RUC)
            return falso

        for estado in ("PENDIENTE", "PAGADA"):
            with patch.object(portal, "Portal", return_value=portal_con(estado)):
                sincronizar(self.organization, desde_anio=2026,
                            hoy=date(2026, 7, 15))

        self.assertEqual(AfpnetDeclaration.objects.filter(taxpayer_id=RUC).count(), 1)
        self.assertTrue(AfpnetDeclaration.objects.get(taxpayer_id=RUC).is_paid)


class PasoDentroDelTrabajoTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(ruc=RUC, name="PATTERN")
        SunatCredential.objects.create(
            organization=self.organization,
            sol_username="USUARIO",
            status=SunatConnectionStatus.CONNECTED,
            last_verified_at=timezone.now(),
        ).set_password("clave-sol")
        self.job = SyncJob.objects.create(
            organization=self.organization,
            kind=JobKind.INITIAL,
            steps=initial_steps(JobKind.INITIAL),
        )

    def test_sin_sesion_el_paso_queda_fallido_con_motivo(self):
        execute_step(self.job, "afpnet")

        paso = self.job.step("afpnet")
        self.assertEqual(paso["status"], StepStatus.FAILED)
        self.assertIn("AFPnet", paso["detail"])

    def test_no_invalida_la_credencial_sol(self):
        """`SourceFailed` y no `LoginFailed`: que AFPnet pida un CAPTCHA no dice
        nada sobre si la clave de SUNAT sirve."""
        execute_step(self.job, "afpnet")

        credencial = self.organization.sunat_credential
        credencial.refresh_from_db()
        self.assertEqual(credencial.status, SunatConnectionStatus.CONNECTED)

    def test_el_paso_se_puede_reintentar_tras_conectar(self):
        execute_step(self.job, "afpnet")

        self.assertTrue(self.job.can_retry("afpnet"))


class EnrolarTests(TestCase):
    """Enrolar a un trabajador no puede depender de que el historial funcione."""

    def setUp(self):
        self.organization = Organization.objects.create(ruc=RUC, name="PATTERN")
        sesion = AfpnetSession.objects.create(
            organization=self.organization, taxpayer_id=RUC
        )
        sesion.marcar_activa({"c": "1"}, "ADM0001")

    def test_el_alta_sobrevive_a_un_historial_que_falla(self):
        """`getSessionOpListJson` responde 500 si no se seleccionó al afiliado
        desde la pantalla de obligación de pago. Perder el historial no puede
        costar el alta del trabajador."""
        from afpnet.models import AfpnetAffiliate
        from afpnet.services import parsers
        from afpnet.services.sync import enrolar_trabajador

        falso = MagicMock()
        falso.consultar_afiliado.return_value = parsers.Afiliado(
            cuspp="999999AAAAA1", numero_documento="12345678",
            nombres="MARIA", apellido_paterno="PEREZ", afp="habitat",
            situacion="Continúa",
        )
        falso.historial_aportes.side_effect = client.AfpnetError("500")

        with patch.object(portal, "Portal", return_value=falso):
            afiliado = enrolar_trabajador(self.organization, "12345678")

        self.assertIsNotNone(afiliado)
        self.assertEqual(AfpnetAffiliate.objects.count(), 1)
        self.assertEqual(afiliado.contributions.count(), 0)


class SesionCaducadaAlEnrolarTests(TestCase):
    """Si la sesión muere a mitad, hay que decirlo como tal.

    Antes salía como fallo genérico del portal —indistinguible de una caída de
    AFPnet— y la sesión seguía marcada como activa, así que la pantalla ofrecía
    sincronizar sobre algo que ya no funcionaba.
    """

    def setUp(self):
        self.organization = Organization.objects.create(ruc=RUC, name="PATTERN")
        self.sesion = AfpnetSession.objects.create(
            organization=self.organization, taxpayer_id=RUC
        )
        self.sesion.marcar_activa({"c": "1"}, "ADM0001")

    def test_se_traduce_a_sin_sesion_y_se_marca_caducada(self):
        from afpnet.services.sync import enrolar_trabajador

        falso = MagicMock()
        falso.consultar_afiliado.side_effect = client.SesionCaducada("caducó")

        with patch.object(portal, "Portal", return_value=falso):
            with self.assertRaises(SinSesion):
                enrolar_trabajador(self.organization, "12345678")

        self.sesion.refresh_from_db()
        self.assertEqual(self.sesion.status, SessionStatus.EXPIRED)
        self.assertEqual(self.sesion.encrypted_cookies, "")

    def test_la_vista_responde_409_con_codigo(self):
        """La interfaz distingue por `code`, no por el texto del mensaje."""
        from django.urls import reverse
        from rest_framework.test import APIClient

        from accounts.models import Membership, Role, User

        user = User.objects.create_user(
            email="dueno@pattern.pe", password="clave-de-pruebas-99",
            email_verified_at=timezone.now(),
        )
        Membership.objects.create(
            user=user, organization=self.organization, role=Role.OWNER
        )
        cliente = APIClient()
        cliente.force_authenticate(user)

        falso = MagicMock()
        falso.consultar_afiliado.side_effect = client.SesionCaducada("caducó")
        with patch.object(portal, "Portal", return_value=falso):
            r = cliente.post(reverse("afpnet:enrolar"), {"documento": "12345678"},
                             format="json")

        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "sin_sesion")
