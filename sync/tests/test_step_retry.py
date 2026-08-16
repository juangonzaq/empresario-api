"""Un paso que falló se puede relanzar solo, sin repetir el resto.

La sincronización completa tarda minutos —el histórico de comprobantes es lo
más caro— así que obligar a repetirla entera porque el buzón se cayó una vez
es cobrarle al usuario diez pasos para arreglar uno. Lo que estos tests
vigilan es que ese atajo no rompa la regla que sostiene todo lo demás: una
sola sesión SOL a la vez por empresa.
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import (
    Membership, Organization, Role, SunatConnectionStatus, SunatCredential, User,
)
from sync.models import JobKind, JobStatus, StepStatus, SyncJob
from sync.services import CannotRetry, execute_step, retry_step
from sync.sources import Cadence, Source

RUC = "20100000001"


def make_org(ruc: str = RUC) -> Organization:
    org = Organization.objects.create(ruc=ruc, name=f"EMPRESA {ruc}")
    credential = SunatCredential(
        organization=org, sol_username="CONSULTA1",
        status=SunatConnectionStatus.CONNECTED,
    )
    credential.set_password("clave-sol")
    credential.save()
    return org


def make_job(org: Organization, **estados: str) -> SyncJob:
    """Un trabajo ya terminado, con el estado que se le pida a cada paso."""
    return SyncJob.objects.create(
        organization=org,
        kind=JobKind.INITIAL,
        status=JobStatus.PARTIAL,
        steps=[
            {"key": key, "label": key, "status": status,
             "detail": "algo se rompió", "started_at": None, "finished_at": None}
            for key, status in estados.items()
        ],
    )


class RetryStepTests(TestCase):
    def setUp(self):
        self.org = make_org()
        patcher = patch("sync.tasks.run_sync_step.apply_async")
        self.addCleanup(patcher.stop)
        self.enqueue = patcher.start()

    def test_relanzar_deja_el_paso_listo_y_lo_encola(self):
        job = make_job(self.org, cpe="completo", mailbox="fallido")

        devuelto = retry_step(self.org, "mailbox")

        self.assertEqual(devuelto.pk, job.pk, "no debe nacer un trabajo nuevo")
        job.refresh_from_db()
        pasos = {s["key"]: s for s in job.steps}
        self.assertEqual(pasos["mailbox"]["status"], StepStatus.PENDING)
        self.assertEqual(pasos["mailbox"]["detail"], "")
        # Y lo que sí trajo datos se queda como estaba: ese es el ahorro.
        self.assertEqual(pasos["cpe"]["status"], StepStatus.DONE)
        self.assertEqual(job.status, JobStatus.QUEUED)
        # Sin cadencia: reintentar un paso fallido repite lo que ese paso
        # tenía que hacer, con la del trabajo al que pertenece.
        self.enqueue.assert_called_once_with((str(job.id), "mailbox", None))

    def test_un_paso_omitido_tambien_se_puede_relanzar(self):
        """Se omiten justo los que quedaron sin correr por culpa de otro."""
        make_job(self.org, mailbox="fallido", cpe="omitido")
        retry_step(self.org, "cpe")
        self.assertEqual(SyncJob.objects.get().steps[1]["status"], StepStatus.PENDING)

    def test_un_paso_que_funciono_no_se_relanza(self):
        make_job(self.org, cpe="completo")
        with self.assertRaises(CannotRetry):
            retry_step(self.org, "cpe")
        self.enqueue.assert_not_called()

    def test_no_se_relanza_nada_con_una_sincronizacion_en_marcha(self):
        """Dos scrapeos a la vez con el mismo usuario SOL se pisan."""
        job = make_job(self.org, cpe="fallido")
        job.status = JobStatus.RUNNING
        job.save(update_fields=["status"])

        with self.assertRaises(CannotRetry):
            retry_step(self.org, "cpe")
        self.enqueue.assert_not_called()

    def test_una_clave_inventada_no_pasa(self):
        make_job(self.org, cpe="fallido")
        with self.assertRaises(CannotRetry):
            retry_step(self.org, "no_existe")

    def test_sin_ningun_trabajo_previo_no_hay_nada_que_reintentar(self):
        with self.assertRaises(CannotRetry):
            retry_step(self.org, "cpe")

    def test_el_trabajo_reabierto_no_se_da_por_abandonado(self):
        """Con el plazo medido desde el alta, reintentar un paso de un trabajo
        de ayer lo dejaba vencido de nacimiento: el siguiente reparto lo mataba
        mientras el worker seguía corriéndolo, y encolaba otro encima."""
        job = make_job(self.org, cpe="fallido")
        ayer = timezone.now() - datetime.timedelta(days=1)
        SyncJob.objects.filter(pk=job.pk).update(created_at=ayer, updated_at=ayer)

        retry_step(self.org, "cpe")

        self.assertTrue(
            SyncJob.objects.filter(organization=self.org).active().exists()
        )


class ExecuteStepTests(TestCase):
    """Correr el paso suelto deja el trabajo con el estado que toca."""

    def setUp(self):
        self.org = make_org()

    def _job(self, **estados):
        job = make_job(self.org, **estados)
        job.reopen_step(next(iter(estados)))
        return job

    def test_un_reintento_exitoso_completa_el_trabajo(self):
        job = self._job(cpe="fallido", analytics="completo")

        with _fuente_falsa("cpe", lambda *_: {"comprobantes": 3}):
            execute_step(job, "cpe")

        job.refresh_from_db()
        pasos = {s["key"]: s for s in job.steps}
        self.assertEqual(pasos["cpe"]["status"], StepStatus.DONE)
        self.assertIn("comprobantes: 3", pasos["cpe"]["detail"])
        # Sin pasos fallidos, el trabajo entero pasa a completo.
        self.assertEqual(job.status, JobStatus.DONE)

    def test_si_vuelve_a_fallar_el_trabajo_queda_parcial(self):
        job = self._job(cpe="fallido", analytics="completo")

        def revienta(*_):
            raise RuntimeError("el portal no respondió")

        with _fuente_falsa("cpe", revienta):
            execute_step(job, "cpe")

        job.refresh_from_db()
        self.assertEqual(job.steps[0]["status"], StepStatus.FAILED)
        self.assertIn("no respondió", job.steps[0]["detail"])
        self.assertEqual(job.status, JobStatus.PARTIAL)

    def test_sin_credenciales_el_paso_queda_omitido(self):
        SunatCredential.objects.filter(organization=self.org).delete()
        self.org.refresh_from_db()
        job = self._job(cpe="fallido")

        execute_step(job, "cpe")

        job.refresh_from_db()
        self.assertEqual(job.steps[0]["status"], StepStatus.SKIPPED)
        self.assertEqual(job.status, JobStatus.FAILED)


def _fuente_falsa(key: str, run):
    """Sustituye una fuente real por una que no sale a SUNAT."""
    fuente = Source(key, key, False, run, frozenset(Cadence.ALL))
    return patch.dict("sync.services.SOURCES_BY_KEY", {key: fuente})


class RetryEndpointTests(TestCase):
    """La ruta que pulsa el botón «Reintentar» de la pantalla de perfil."""

    def setUp(self):
        self.client = APIClient()
        self.org = make_org()
        self.owner = User.objects.create_user(
            email="dueno@empresa.pe", password="clave-de-pruebas-99",
            email_verified_at=timezone.now(),
        )
        Membership.objects.create(
            user=self.owner, organization=self.org, role=Role.OWNER
        )
        patcher = patch("sync.tasks.run_sync_step.apply_async")
        self.addCleanup(patcher.stop)
        self.enqueue = patcher.start()

    def _url(self, key: str) -> str:
        return reverse("sync:step-retry", args=[key])

    def test_devuelve_el_trabajo_reabierto(self):
        make_job(self.org, cpe="fallido")
        self.client.force_authenticate(self.owner)

        response = self.client.post(self._url("cpe"))

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["status"], JobStatus.QUEUED)
        self.assertEqual(response.data["steps"][0]["status"], StepStatus.PENDING)

    def test_un_paso_que_no_fallo_devuelve_409(self):
        make_job(self.org, cpe="completo")
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(self._url("cpe")).status_code, 409)

    def test_el_estado_dice_que_pasos_se_pueden_relanzar(self):
        """El botón se pinta con esto: si el front decidiera por su cuenta,
        acabaría ofreciendo reintentos que el API rechaza."""
        make_job(self.org, cpe="fallido", analytics="completo")
        self.client.force_authenticate(self.owner)

        pasos = self.client.get(reverse("sync:status")).data["steps"]

        self.assertEqual(
            {p["key"]: p["retryable"] for p in pasos},
            {"cpe": True, "analytics": False},
        )

    def test_sin_sesion_no_se_relanza_nada(self):
        make_job(self.org, cpe="fallido")
        self.assertEqual(self.client.post(self._url("cpe")).status_code, 401)

    def test_un_miembro_sin_permiso_de_gestion_no_puede(self):
        otro = User.objects.create_user(
            email="mira@empresa.pe", password="clave-de-pruebas-99",
            email_verified_at=timezone.now(),
        )
        Membership.objects.create(
            user=otro, organization=self.org, role=Role.VIEWER
        )
        make_job(self.org, cpe="fallido")
        self.client.force_authenticate(otro)
        self.assertEqual(self.client.post(self._url("cpe")).status_code, 403)
