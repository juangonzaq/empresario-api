"""Cambiar o quitar la clave SOL a mitad de una sincronización.

Caso real: el usuario conectó con una clave equivocada, la sincronización
inicial arrancó, y al corregirla el trabajo viejo intentó anotar «rechazada»
sobre una credencial que ya no existía. `save(update_fields=...)` reventó con
`NotUpdated`, la tarea de Celery murió y el trabajo quedó en «ejecutando» sin
nadie detrás: la pantalla decía «4 de 12 · Cancelar» y no dejaba relanzar.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

from django.urls import reverse

from accounts.models import SunatCredential, SunatConnectionStatus
from accounts.services import consent
from core.testing import TenantAPITestCase
from sync.models import JobKind, JobStatus, StepStatus, SyncJob
from sync.services import Credentials, _run_source, execute
from sync.sources import LoginFailed, SOURCES_BY_KEY, initial_steps


def fuente(clave: str, run):
    return replace(SOURCES_BY_KEY[clave], run=run)


class CredencialBorradaEnMedioTests(TenantAPITestCase):
    def setUp(self):
        self.credencial = SunatCredential.objects.create(
            organization=self.organization, sol_username="CONSULTA1",
            status=SunatConnectionStatus.PENDING,
        )
        self.credencial.set_password("clave-mala")
        self.credencial.save()
        self.job = SyncJob.objects.create(
            organization=self.organization, kind=JobKind.INITIAL,
            steps=initial_steps(JobKind.INITIAL),
        )
        self.credenciales = Credentials(ruc=self.RUC, username="CONSULTA1", password="clave-mala")

    def test_un_rechazo_sobre_una_credencial_borrada_no_tumba_el_trabajo(self):
        def run(_c, _k):
            # El usuario desconectó mientras el paso corría.
            SunatCredential.objects.filter(organization=self.organization).delete()
            raise LoginFailed("clave incorrecta")

        fatal = _run_source(self.job, fuente("mailbox", run), self.credenciales)

        self.assertEqual(fatal, "clave incorrecta")
        self.assertEqual(self.job._step("mailbox")["status"], StepStatus.FAILED)

    def test_un_rechazo_marca_la_credencial_que_exista_ahora(self):
        def run(_c, _k):
            raise LoginFailed("clave incorrecta")

        _run_source(self.job, fuente("mailbox", run), self.credenciales)
        self.credencial.refresh_from_db()
        self.assertEqual(self.credencial.status, SunatConnectionStatus.INVALID)
        self.assertEqual(self.credencial.last_error, "clave incorrecta")

    def test_un_exito_no_pisa_una_credencial_ya_conectada(self):
        self.credencial.status = SunatConnectionStatus.CONNECTED
        self.credencial.last_verified_at = None
        self.credencial.save()
        _run_source(self.job, fuente("mailbox", lambda c, k: {"mensajes": 1}), self.credenciales)
        self.credencial.refresh_from_db()
        self.assertIsNone(self.credencial.last_verified_at)  # no se tocó

    def test_un_exito_confirma_la_pendiente(self):
        _run_source(self.job, fuente("mailbox", lambda c, k: {"mensajes": 1}), self.credenciales)
        self.credencial.refresh_from_db()
        self.assertEqual(self.credencial.status, SunatConnectionStatus.CONNECTED)

    def test_un_error_fuera_de_los_pasos_cierra_el_trabajo(self):
        with patch("sync.services._execute", side_effect=RuntimeError("la base se fue")):
            execute(self.job)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, JobStatus.FAILED)
        self.assertIn("la base se fue", self.job.error)
        self.assertFalse(self.job.is_unfinished)


class CambiarClaveCancelaLaCorridaTests(TenantAPITestCase):
    """Corregir la clave debe cortar el trabajo que sigue probando la vieja."""

    def _conectar(self, clave: str):
        with patch("sync.tasks.run_sync_job") as tarea:
            tarea.apply_async.return_value = None
            return self.client.post(reverse("accounts:sunat-connection"), {
                "sol_username": "CONSULTA1", "sol_password": clave,
                "authorization_accepted": True, "authorization_version": consent.VERSION,
            }, format="json")

    def test_reconectar_cancela_el_trabajo_en_curso_y_encola_otro(self):
        primera = self._conectar("clave-mala")
        self.assertEqual(primera.status_code, 202, primera.data)
        viejo = SyncJob.objects.get(pk=primera.data["sync_job"])
        viejo.start()

        segunda = self._conectar("clave-buena")
        self.assertEqual(segunda.status_code, 202, segunda.data)

        viejo.refresh_from_db()
        self.assertEqual(viejo.status, JobStatus.CANCELLED)
        self.assertNotEqual(segunda.data["sync_job"], str(viejo.pk))
        nuevo = SyncJob.objects.get(pk=segunda.data["sync_job"])
        self.assertEqual(nuevo.status, JobStatus.QUEUED)

    def test_desconectar_cancela_el_trabajo_en_curso(self):
        primera = self._conectar("clave-mala")
        viejo = SyncJob.objects.get(pk=primera.data["sync_job"])
        viejo.start()

        self.assertEqual(self.client.delete(reverse("accounts:sunat-connection")).status_code, 204)
        viejo.refresh_from_db()
        self.assertEqual(viejo.status, JobStatus.CANCELLED)
