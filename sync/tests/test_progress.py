"""La barra por paso: un paso largo anota «n de N» y puede parar si lo cancelan."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from sync.models import JobStatus, StepStatus, SyncJob
from sync.progress import report_progress
from sync.services import _run_source
from sync.sources import Source

from .test_step_retry import make_job, make_org

CREDS = SimpleNamespace(ruc="20100000009", username="U", password="P")


class ProgressTests(TestCase):
    def setUp(self):
        self.org = make_org()
        self.job = make_job(self.org, intel=StepStatus.PENDING)
        self.job.status = JobStatus.RUNNING
        self.job.save()

    def test_el_paso_anota_su_avance_en_el_trabajo(self):
        vistos = []

        def corre(creds, cadence):
            for i in range(3):
                vistos.append(report_progress(i, 3, f"{i} de 3"))
            return {"ok": 1}

        _run_source(self.job, Source("intel", "Intel", False, corre, frozenset()), CREDS)
        self.job.refresh_from_db()
        paso = self.job.step("intel")
        self.assertEqual(paso["status"], StepStatus.DONE)
        self.assertEqual(paso["progress"], {"done": 2, "total": 3})
        self.assertEqual(vistos, [True, True, True])

    def test_sin_receptor_no_pasa_nada(self):
        self.assertTrue(report_progress(1, 2))

    def test_la_cancelacion_llega_al_paso(self):
        def corre(creds, cadence):
            sigue = report_progress(0, 2)
            SyncJob.objects.filter(pk=self.job.pk).update(status=JobStatus.CANCELLED)
            return {"sigue_antes": sigue, "sigue_despues": report_progress(1, 2)}

        _run_source(self.job, Source("intel", "Intel", False, corre, frozenset()), CREDS)
        self.job.refresh_from_db()
        self.assertIn("sigue_antes: True, sigue_despues: False", self.job.step("intel")["detail"])
