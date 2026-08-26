"""Una fuente que no trae nada tiene que verse como fallo, no como éxito.

Los sincronizadores de ficha RUC y REMYPE recorren una lista de RUC y anotan
los que fallan en un contador, sin levantar excepción: así una empresa caída
no interrumpe el recorrido de las demás. El trabajo de sincronización les pasa
un solo RUC, y durante un tiempo se tragó ese contador — la pantalla mostraba
«Listo — 0 consultados» en verde mientras el navegador se había caído al
arrancar. Un fallo invisible es peor que un fallo ruidoso: nadie lo mira.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from accounts.models import SunatCredential, SunatConnectionStatus
from core.testing import TenantAPITestCase

from remype.models import RemypeCheck
from ruc_profile.models import RucSnapshot
from sync.models import JobKind, StepStatus, SyncJob
from sync.services import Credentials, _motivo_amigable, _run_source
from sync.sources import (
    LoginFailed, SOURCES_BY_KEY, Source, SourceFailed, _compliance, _remype,
    _ruc_profile, initial_steps,
)

RUC = "20604442533"


@dataclass
class Creds:
    ruc: str = RUC
    username: str = "CONSULTA1"
    password: str = "clave-sol"


@dataclass
class FakeResult:
    """Lo que devuelven los sincronizadores reales, en lo que aquí importa."""

    failed: int = 0
    checked: int = 0
    captured: int = 0


class RemypeFailureTests(TestCase):
    def test_fallo_levanta_excepcion_con_el_motivo_guardado(self):
        RemypeCheck.objects.create(
            ruc=RUC, checked_on=timezone.localdate(), succeeded=False,
            is_registered=False, changed=False,
            message="Could not open the REMYPE page: browser has been closed",
            payload={},
        )
        with patch("remype.services.RemypeSynchronizer") as sync:
            sync.return_value.run.return_value = FakeResult(failed=1)
            with self.assertRaises(SourceFailed) as capturado:
                _remype(Creds(), "manual")

        self.assertIn("browser has been closed", str(capturado.exception))

    def test_sin_motivo_guardado_cae_al_texto_generico(self):
        with patch("remype.services.RemypeSynchronizer") as sync:
            sync.return_value.run.return_value = FakeResult(failed=1)
            with self.assertRaises(SourceFailed) as capturado:
                _remype(Creds(), "manual")

        self.assertIn("REMYPE", str(capturado.exception))

    def test_sin_fallos_devuelve_el_conteo(self):
        with patch("remype.services.RemypeSynchronizer") as sync:
            sync.return_value.run.return_value = FakeResult(checked=1)
            self.assertEqual(_remype(Creds(), "manual"), {"consultados": 1})


class RucProfileFailureTests(TestCase):
    def test_fallo_levanta_excepcion_con_el_motivo_guardado(self):
        RucSnapshot.objects.create(
            ruc=RUC, captured_on=timezone.localdate(), succeeded=False,
            changed=False, error="SUNAT no respondió",
        )
        with patch("ruc_profile.services.RucProfileSynchronizer") as sync:
            sync.return_value.run.return_value = FakeResult(failed=1)
            with self.assertRaises(SourceFailed) as capturado:
                _ruc_profile(Creds(), "manual")

        self.assertIn("SUNAT no respondió", str(capturado.exception))

    def test_sin_fallos_devuelve_el_conteo(self):
        with patch("ruc_profile.services.RucProfileSynchronizer") as sync:
            sync.return_value.run.return_value = FakeResult(captured=1)
            self.assertEqual(_ruc_profile(Creds(), "manual"), {"capturados": 1})


class ComplianceLoginFailureTests(TestCase):
    """Solo el rechazo de SOL puede costar la credencial.

    Antes, *cualquier* tropiezo del navegador en el perfil de cumplimiento
    llegaba como ``LoginFailed``: eso marca la credencial de la empresa como
    inválida y salta el buzón, los comprobantes, SUNAFIL y el ITF. Una campaña
    que SUNAT superpone al menú —un pop-up— dejaba así la sincronización
    entera en nada, y al usuario leyendo que su clave SOL estaba rechazada.
    """

    def _login_falla(self, excepcion):
        from compliance_profile.services import CompliancePortalClient

        with patch.object(CompliancePortalClient, "login", side_effect=excepcion):
            return _compliance(Creds(), "manual")

    def test_una_clave_rechazada_corta_el_trabajo(self):
        from compliance_profile.services import ComplianceLoginRejected

        with self.assertRaises(LoginFailed) as capturado:
            self._login_falla(ComplianceLoginRejected("SUNAT no aceptó la clave."))
        self.assertIn("no aceptó", str(capturado.exception))

    def test_un_tropiezo_del_portal_solo_tumba_su_paso(self):
        from compliance_profile.services import CompliancePortalError

        with self.assertRaises(SourceFailed) as capturado:
            self._login_falla(
                CompliancePortalError("Browser automation failed: Timeout 30000ms")
            )
        self.assertNotIsInstance(capturado.exception, LoginFailed)


def fuente_que_falla(clave: str, error: Exception) -> Source:
    """La fuente real, con su `run` cambiado por uno que falla.

    `Source` es un dataclass congelado —no se le puede parchear un atributo—,
    así que se copia con `replace`. Se parte de la real para no inventar sus
    señas: lo que importa del caso es su `key` y su `needs_sol`.
    """

    def run(_credenciales, _cadencia):
        raise error

    return replace(SOURCES_BY_KEY[clave], run=run)


class ClaveBuenaPortalMaloTests(TenantAPITestCase):
    """Un portal que falla después de que otro entró no cuesta la credencial.

    Caso real (RUC 20610172220): el buzón trajo 225 mensajes y los
    comprobantes 1.254 con la misma clave SOL; SUNAFIL falló a continuación
    —su cliente no distingue «me rechazaron» de «no llegué a la casilla», y su
    propio mensaje lo admite—, la credencial quedó marcada como rechazada y el
    ITF se saltó entero. La empresa se quedó sin sus movimientos bancarios y
    con un aviso de credenciales inválidas que no era cierto.
    """

    def setUp(self):
        self.credencial = SunatCredential.objects.create(
            organization=self.organization, sol_username="CONSULTA1",
            status=SunatConnectionStatus.CONNECTED,
        )
        self.credencial.set_password("clave-sol")
        self.credencial.save()

        self.job = SyncJob.objects.create(
            organization=self.organization, kind=JobKind.MANUAL,
            steps=initial_steps(JobKind.MANUAL),
        )
        self.credenciales = Credentials(
            ruc=self.RUC, username="CONSULTA1", password="clave-sol"
        )

    def _correr(self, clave: str, error: Exception) -> str:
        return _run_source(self.job, fuente_que_falla(clave, error), self.credenciales)

    def test_no_invalida_la_clave_si_otra_fuente_ya_entro(self):
        self.job.mark_step("mailbox", StepStatus.DONE, "mensajes: 225")

        fatal = self._correr(
            "sunafil", LoginFailed("Login did not reach the casilla.")
        )

        self.credencial.refresh_from_db()
        self.assertEqual(self.credencial.status, SunatConnectionStatus.CONNECTED)
        # Y sobre todo: no es fatal, así que el ITF que viene detrás sí corre.
        self.assertEqual(fatal, "")
        paso = self.job.step("sunafil")
        self.assertEqual(paso["status"], StepStatus.FAILED)
        self.assertIn("casilla", paso["detail"])

    def test_sin_esa_prueba_si_se_invalida(self):
        """Primera fuente con clave del trabajo: aquí sí hay que creerle."""
        fatal = self._correr("mailbox", LoginFailed("SUNAT rechazó la clave."))

        self.credencial.refresh_from_db()
        self.assertEqual(self.credencial.status, SunatConnectionStatus.INVALID)
        self.assertTrue(fatal)
        self.assertEqual(self.job.step("mailbox")["detail"], "Credenciales rechazadas")

    def test_los_pasos_sin_clave_no_cuentan_como_prueba(self):
        """La ficha RUC no lleva clave: que funcione no dice nada de ella."""
        self.job.mark_step("ruc_profile", StepStatus.DONE, "capturados: 1")

        self._correr("sunafil", LoginFailed("Login did not reach the casilla."))

        self.credencial.refresh_from_db()
        self.assertEqual(self.credencial.status, SunatConnectionStatus.INVALID)


class MotivoAmigableTests(TenantAPITestCase):
    """Un paso caído explica qué pasó en palabras, no con el traceback.

    Caso real: el buzón se quedó esperando a ww1.sunat.gob.pe y el paso
    mostraba «HTTPSConnectionPool(...) Read timed out. (read timeout=60)».
    """

    def setUp(self):
        self.job = SyncJob.objects.create(
            organization=self.organization, kind=JobKind.MANUAL,
            steps=initial_steps(JobKind.MANUAL),
        )
        self.credenciales = Credentials(
            ruc=self.RUC, username="CONSULTA1", password="clave-sol"
        )

    def test_timeout_de_sunat_se_explica_y_no_corta_el_trabajo(self):
        from requests.exceptions import ReadTimeout

        error = ReadTimeout(
            "HTTPSConnectionPool(host='ww1.sunat.gob.pe', port=443): "
            "Read timed out. (read timeout=60)"
        )
        fatal = _run_source(self.job, fuente_que_falla("mailbox", error), self.credenciales)

        self.assertEqual(fatal, "")
        paso = self.job.step("mailbox")
        self.assertEqual(paso["status"], StepStatus.FAILED)
        self.assertIn("tardó demasiado", paso["detail"])
        self.assertNotIn("HTTPSConnectionPool", paso["detail"])

    def test_cada_familia_de_error_tiene_su_frase(self):
        from requests.exceptions import ConnectionError as RqConnectionError, SSLError

        self.assertIn("conectar", _motivo_amigable(RqConnectionError("boom")))
        self.assertIn("segura", _motivo_amigable(SSLError("boom")))
        self.assertIn("inesperado", _motivo_amigable(KeyError("x")))
        # Lo que la fuente ya redactó se respeta tal cual.
        self.assertEqual(_motivo_amigable(SourceFailed("Sin filas hoy.")), "Sin filas hoy.")
