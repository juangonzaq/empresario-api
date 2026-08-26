"""Cancelar una sincronización en curso.

Una sincronización colgada dejaba la pantalla «Sincronizando…» sin salida:
el arranque manual se niega mientras haya trabajo activo, y el activo no
terminaba. Cancelar es cooperativo —el paso en curso termina lo suyo— pero
libera a la empresa al instante: el trabajo deja de contar como activo.
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from accounts.models import Organization, SunatConnectionStatus, SunatCredential
from sync.models import JobStatus, StepStatus, SyncJob
from sync.services import CannotRetry, cancel_sync, execute
from sync.sources import initial_steps

RUC = "20100000009"


def make_org() -> Organization:
    org = Organization.objects.create(ruc=RUC, name="EMPRESA CANCEL")
    credential = SunatCredential(
        organization=org, sol_username="CONSULTA1",
        status=SunatConnectionStatus.CONNECTED,
    )
    credential.set_password("clave-sol")
    credential.save()
    return org


class CancelTests(TestCase):
    def setUp(self):
        self.org = make_org()

    def test_cancel_marks_pending_steps_and_frees_the_company(self):
        job = SyncJob.objects.create(
            organization=self.org, status=JobStatus.QUEUED, steps=initial_steps()
        )
        cancelled = cancel_sync(self.org)
        self.assertEqual(cancelled.pk, job.pk)
        self.assertEqual(cancelled.status, JobStatus.CANCELLED)
        self.assertTrue(all(
            s["status"] == StepStatus.SKIPPED for s in cancelled.steps
        ))
        # Deja de contar como activo: se puede lanzar otra sincronización.
        self.assertFalse(SyncJob.objects.unfinished().exists())

    def test_cancel_without_running_job_raises(self):
        with self.assertRaises(CannotRetry):
            cancel_sync(self.org)

    def test_execute_stops_before_running_anything_when_cancelled(self):
        job = SyncJob.objects.create(
            organization=self.org, status=JobStatus.QUEUED, steps=initial_steps()
        )
        job.cancel()
        with patch("sync.services._run_source") as run:
            result = execute(job)
        run.assert_not_called()
        self.assertEqual(result.status, JobStatus.CANCELLED)

    def test_cancelled_steps_can_be_retried_later(self):
        job = SyncJob.objects.create(
            organization=self.org, status=JobStatus.QUEUED, steps=initial_steps()
        )
        job.cancel()
        # Omitido por cancelación es exactamente el caso que interesa relanzar.
        self.assertTrue(job.can_retry(job.steps[0]["key"]))
