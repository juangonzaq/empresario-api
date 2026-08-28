"""La sincronización programada recorre TODAS las empresas conectadas.

El fallo que estos tests vigilan es silencioso y caro: que Beat sincronice a
una empresa y deje al resto con datos viejos sin que nadie se entere.
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from accounts.models import (
    Membership, Organization, Role, SunatConnectionStatus, SunatCredential, User,
)
from sync.models import JobKind, JobStatus, SyncJob
from sync.sources import SOURCES, Cadence, sources_for
from sync.tasks import sync_all

KEY = "D__dbkJT6pvD6tQBqyl37GUdmyuBp3ZPYM220eV9y6Q="


def make_org(ruc: str, status=SunatConnectionStatus.CONNECTED, with_credential=True):
    org = Organization.objects.create(ruc=ruc, name=f"EMPRESA {ruc}")
    user = User.objects.create_user(
        email=f"{ruc}@empresa.pe", password="clave-de-pruebas-99",
        email_verified_at=timezone.now(),
    )
    Membership.objects.create(user=user, organization=org, role=Role.OWNER)
    if with_credential:
        credential = SunatCredential(
            organization=org, sol_username="CONSULTA1", status=status
        )
        credential.set_password("clave-sol")
        credential.save()
    return org


class CadenceTests(TestCase):
    def test_daily_skips_what_barely_changes(self):
        keys = {s.key for s in sources_for(Cadence.DAILY)}
        # Lo que se mueve a diario y trae plazos.
        self.assertIn("cpe", keys)
        self.assertIn("mailbox", keys)
        self.assertIn("sunafil", keys)
        # Lo que cambia de mes a mes no se pide todos los días.
        self.assertNotIn("itf", keys)
        self.assertNotIn("compliance", keys)
        self.assertNotIn("ruc_profile", keys)

    def test_monthly_brings_the_slow_sources(self):
        keys = {s.key for s in sources_for(Cadence.MONTHLY)}
        self.assertEqual(
            keys,
            # AFPnet entra aquí y no en la diaria por dos motivos: los aportes
            # se declaran una vez al mes, y cada sesión cuesta que una persona
            # resuelva un CAPTCHA. RHE además re-barre el ejercicio en curso:
            # la lista de pagos y las reversiones cambian tras la emisión.
            {"itf", "compliance", "ruc_profile", "remype", "tributos", "analytics",
             "afpnet", "rhe", "declaraciones", "renta_anual"},
        )

    def test_afpnet_no_se_pide_a_diario(self):
        """Su sesión la abre una persona resolviendo un CAPTCHA: pedirla cada
        día la gastaría sin traer nada nuevo."""
        keys = {s.key for s in sources_for(Cadence.DAILY)}

        self.assertNotIn("afpnet", keys)

    def test_manual_and_initial_run_everything(self):
        for cadence in (Cadence.MANUAL, Cadence.INITIAL):
            self.assertEqual(
                len(sources_for(cadence)), len(SOURCES),
                f"{cadence} no corre todas las fuentes",
            )

    def test_la_lectura_del_buzon_corre_tras_el_buzon_y_no_al_mes(self):
        """Sin este paso, «Resumen», «Casos» y «Decisiones» quedaban vacíos:
        nada disparaba el análisis. Va después del buzón, en la diaria y en la
        inicial; en la mensual no hay buzón nuevo que leer."""
        daily = [s.key for s in sources_for(Cadence.DAILY)]
        self.assertIn("intel", daily)
        self.assertGreater(daily.index("intel"), daily.index("mailbox"))
        self.assertNotIn("intel", {s.key for s in sources_for(Cadence.MONTHLY)})

    def test_analytics_always_runs_last(self):
        for cadence in (Cadence.INITIAL, Cadence.DAILY, Cadence.MONTHLY):
            sources = sources_for(cadence)
            self.assertEqual(
                sources[-1].key, "analytics",
                "la analítica debe recalcularse después de traer los datos",
            )


class FanOutTests(TestCase):
    """`sync_all` es lo que dispara Beat."""

    def setUp(self):
        self.enqueued: list[tuple[str, int]] = []
        patcher = patch(
            "sync.tasks.run_sync_job.apply_async",
            side_effect=lambda args, countdown=0, **kw: self.enqueued.append(
                (args[0], countdown)
            ),
        )
        self.addCleanup(patcher.stop)
        patcher.start()

    def _jobs_by_ruc(self):
        return {j.organization.ruc: j for j in SyncJob.objects.select_related("organization")}

    def test_every_connected_company_gets_its_own_job(self):
        for ruc in ("20100000001", "20200000002", "20300000003"):
            make_org(ruc)

        result = sync_all(JobKind.DAILY)

        self.assertEqual(result["encoladas"], 3)
        jobs = self._jobs_by_ruc()
        self.assertEqual(set(jobs), {"20100000001", "20200000002", "20300000003"})
        self.assertTrue(all(j.kind == JobKind.DAILY for j in jobs.values()))

    def test_all_companies_are_enqueued_at_once(self):
        """Sin retardo por empresa: quien limita es la concurrencia del worker.

        El retardo lineal de 2 minutos hacía que con mil empresas la última
        arrancara 33 horas después, o sea nunca.
        """
        for ruc in ("20100000001", "20200000002", "20300000003"):
            make_org(ruc)
        sync_all(JobKind.DAILY)

        self.assertEqual(len(self.enqueued), 3)
        self.assertTrue(all(c == 0 for _, c in self.enqueued))

    def test_it_warns_when_the_batch_does_not_fit_in_its_window(self):
        """Que no se sincronicen las últimas es un fallo silencioso."""
        for i in range(3):
            make_org(f"2010000000{i}")
        with patch("sync.tasks.SYNC_CONCURRENCY", 1), \
             patch("sync.tasks.MINUTOS_POR_EMPRESA", 600), \
             self.assertLogs("sync.tasks", level="ERROR") as logs:
            sync_all(JobKind.DAILY)
        self.assertIn("no entra en", " ".join(logs.output))

    def test_company_without_credentials_is_skipped(self):
        make_org("20100000001")
        make_org("20200000002", with_credential=False)

        sync_all(JobKind.DAILY)

        self.assertEqual(set(self._jobs_by_ruc()), {"20100000001"})

    def test_rejected_credentials_are_not_retried_every_night(self):
        """Insistir con una clave rechazada puede bloquear el usuario SOL."""
        make_org("20100000001")
        make_org("20200000002", status=SunatConnectionStatus.INVALID)

        result = sync_all(JobKind.DAILY)

        self.assertEqual(set(self._jobs_by_ruc()), {"20100000001"})
        # Y se reporta, para que no queden empresas sin sincronizar en silencio.
        self.assertEqual(result["con_credenciales_rechazadas"], 1)

    def test_a_company_already_syncing_is_left_alone(self):
        org = make_org("20100000001")
        SyncJob.objects.create(
            organization=org, kind=JobKind.INITIAL, status=JobStatus.RUNNING
        )

        result = sync_all(JobKind.DAILY)

        self.assertEqual(result["encoladas"], 0)
        self.assertEqual(result["omitidas_en_curso"], 1)
        self.assertEqual(SyncJob.objects.count(), 1)

    def test_daily_job_only_carries_the_daily_steps(self):
        make_org("20100000001")
        sync_all(JobKind.DAILY)

        job = SyncJob.objects.get()
        keys = {s["key"] for s in job.steps}
        self.assertNotIn("itf", keys)
        self.assertIn("cpe", keys)
        self.assertEqual(len(job.steps), len(sources_for(Cadence.DAILY)))

    def test_no_companies_is_not_an_error(self):
        self.assertEqual(sync_all(JobKind.DAILY)["encoladas"], 0)


class StaleJobTests(TestCase):
    """Un trabajo abandonado no puede dejar a una empresa sin sincronizar."""

    def setUp(self):
        self.org = make_org("20100000001")
        patcher = patch("sync.tasks.run_sync_job.apply_async")
        self.addCleanup(patcher.stop)
        self.enqueue = patcher.start()

    def _abandoned(self, status=JobStatus.QUEUED, hours=4):
        job = SyncJob.objects.create(
            organization=self.org, kind=JobKind.INITIAL, status=status,
            steps=[{"key": "cpe", "label": "Comprobantes", "status": "pendiente"}],
        )
        # Las marcas de tiempo son automáticas: se reescriben sin pasar por
        # save(). Abandonado significa «nadie lo ha tocado desde entonces», así
        # que el que cuenta para el plazo es `updated_at`.
        SyncJob.objects.filter(pk=job.pk).update(
            created_at=timezone.now() - datetime.timedelta(hours=hours),
            updated_at=timezone.now() - datetime.timedelta(hours=hours),
        )
        return job

    def test_a_queued_job_nobody_took_stops_blocking(self):
        abandoned = self._abandoned()

        result = sync_all(JobKind.DAILY)

        abandoned.refresh_from_db()
        self.assertEqual(abandoned.status, JobStatus.FAILED)
        self.assertIn("a medias", abandoned.error)
        # Y la empresa vuelve a entrar en la rueda.
        self.assertEqual(result["encoladas"], 1)
        self.assertEqual(SyncJob.objects.count(), 2)

    def test_a_worker_that_died_mid_run_also_releases(self):
        abandoned = self._abandoned(status=JobStatus.RUNNING)
        sync_all(JobKind.DAILY)
        abandoned.refresh_from_db()
        self.assertEqual(abandoned.status, JobStatus.FAILED)

    def test_a_job_still_within_time_is_respected(self):
        # Dos minutos sin latido caben en el plazo (5 min): sigue vivo.
        running = self._abandoned(status=JobStatus.RUNNING, hours=2 / 60)

        result = sync_all(JobKind.DAILY)

        running.refresh_from_db()
        self.assertEqual(running.status, JobStatus.RUNNING)
        self.assertEqual(result["encoladas"], 0)
        self.assertEqual(result["omitidas_en_curso"], 1)

    def test_pending_steps_of_an_abandoned_job_are_not_left_running(self):
        abandoned = self._abandoned(status=JobStatus.RUNNING)
        abandoned.steps[0]["status"] = "ejecutando"
        abandoned.save(update_fields=["steps"])

        sync_all(JobKind.DAILY)

        abandoned.refresh_from_db()
        # La pantalla no debe seguir mostrando un paso «trayendo…» eterno.
        self.assertEqual(abandoned.steps[0]["status"], "omitido")

    def test_a_long_job_that_keeps_advancing_is_not_reclaimed(self):
        """La primera carga de una empresa con años de comprobantes puede
        pasar de tres horas. Mientras siga marcando pasos está viva, y matarla
        a mitad dejaría los datos incompletos sin que nadie lo pidiera."""
        viejo = self._abandoned(status=JobStatus.RUNNING, hours=5)
        viejo.mark_step("cpe", "ejecutando")  # acaba de dar señales de vida

        result = sync_all(JobKind.DAILY)

        viejo.refresh_from_db()
        self.assertEqual(viejo.status, JobStatus.RUNNING)
        self.assertEqual(result["omitidas_en_curso"], 1)

    def test_manual_start_also_releases_a_stuck_job(self):
        from sync.services import start_sync

        self._abandoned()
        job = start_sync(self.org, kind=JobKind.MANUAL)
        self.assertEqual(job.kind, JobKind.MANUAL)
        self.assertEqual(SyncJob.objects.filter(status=JobStatus.FAILED).count(), 1)
