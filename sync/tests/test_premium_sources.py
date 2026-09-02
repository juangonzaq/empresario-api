"""Los pasos de IA corren solo con plan de pago; sin él, quedan Omitidos."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from accounts.models import Organization, SunatConnectionStatus, SunatCredential
from billing.services import ensure_subscription
from sync.models import JobStatus, StepStatus, SyncJob
from sync.services import execute
from sync.sources import SOURCES_BY_KEY, initial_steps

RUC = "20100000011"


def make_org() -> Organization:
    org = Organization.objects.create(ruc=RUC, name="EMPRESA PREMIUM")
    credential = SunatCredential(
        organization=org, sol_username="CONSULTA1",
        status=SunatConnectionStatus.CONNECTED,
    )
    credential.set_password("clave-sol")
    credential.save()
    return org


class PremiumSourceTests(TestCase):
    def setUp(self):
        self.org = make_org()

    def _job(self) -> SyncJob:
        return SyncJob.objects.create(
            organization=self.org, status=JobStatus.QUEUED, steps=initial_steps(),
        )

    def test_intel_es_premium(self):
        self.assertTrue(SOURCES_BY_KEY["intel"].premium)

    def test_en_prueba_gratuita_intel_queda_omitido(self):
        job = self._job()
        with patch("sync.services._run_source", return_value="") as run:
            resultado = execute(job)
        paso = next(s for s in resultado.steps if s["key"] == "intel")
        self.assertEqual(paso["status"], StepStatus.SKIPPED)
        self.assertEqual(paso["detail"], "Disponible con el plan de pago")
        # Los pasos no-premium sí corrieron.
        self.assertNotIn("intel", [c.args[1].key for c in run.call_args_list])
        self.assertTrue(run.called)

    def test_con_plan_pagado_intel_corre(self):
        sub = ensure_subscription(self.org)
        sub.current_period_end = timezone.now() + timedelta(days=30)
        sub.save(update_fields=["current_period_end"])
        job = self._job()
        with patch("sync.services._run_source", return_value="") as run:
            execute(job)
        self.assertIn("intel", [c.args[1].key for c in run.call_args_list])
