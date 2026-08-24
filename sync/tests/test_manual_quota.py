"""Tope diario de sincronizaciones manuales, cargo por exceso e historial."""

from __future__ import annotations

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.tests.test_tenancy import make_org, make_user
from billing.models import UsageCharge
from sync.models import JobKind, JobStatus, SyncJob
from sync.services import SyncLimitReached, manual_quota, start_manual_sync


def _finished_manual(org, n=1):
    """Sincronizaciones manuales ya terminadas hoy (no cuentan como activas)."""
    now = timezone.now()
    for _ in range(n):
        SyncJob.objects.create(
            organization=org, kind=JobKind.MANUAL, status=JobStatus.DONE,
            steps=[], started_at=now, finished_at=now,
        )


class ManualQuotaTests(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@sync.pe")
        self.org = make_org("20100000001", self.owner)

    def test_default_limit_is_two(self):
        q = manual_quota(self.org)
        self.assertEqual((q["used"], q["limit"], q["remaining"]), (0, 2, 2))

    def test_admin_override_raises_the_limit(self):
        self.org.manual_sync_daily_limit = 5
        self.org.save(update_fields=["manual_sync_daily_limit"])
        self.assertEqual(manual_quota(self.org)["limit"], 5)

    def test_under_limit_is_free(self):
        _finished_manual(self.org, 1)
        job, charged, quota = start_manual_sync(self.org, requested_by=self.owner)
        self.assertFalse(charged)
        self.assertEqual(UsageCharge.objects.filter(organization=self.org).count(), 0)
        self.assertEqual(job.kind, JobKind.MANUAL)

    def test_over_limit_needs_acceptance(self):
        _finished_manual(self.org, 2)
        with self.assertRaises(SyncLimitReached):
            start_manual_sync(self.org, requested_by=self.owner)

    def test_over_limit_with_acceptance_charges_five_soles(self):
        _finished_manual(self.org, 2)
        job, charged, quota = start_manual_sync(
            self.org, requested_by=self.owner, accept_charge=True,
        )
        self.assertTrue(charged)
        charge = UsageCharge.objects.get(organization=self.org)
        self.assertEqual(str(charge.amount), "5.00")
        self.assertEqual(charge.reference, str(job.id))

    def test_running_job_is_not_double_charged(self):
        _finished_manual(self.org, 2)
        # Primera manual con cargo crea un trabajo activo (en cola).
        start_manual_sync(self.org, requested_by=self.owner, accept_charge=True)
        # Con un trabajo activo, pedir otra devuelve el mismo sin cobrar de nuevo.
        _, charged, _ = start_manual_sync(self.org, requested_by=self.owner, accept_charge=True)
        self.assertFalse(charged)
        self.assertEqual(UsageCharge.objects.filter(organization=self.org).count(), 1)


class SyncHistoryApiTests(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@sync.pe")
        self.org = make_org("20100000001", self.owner)
        self.client.force_authenticate(self.owner)

    def test_history_returns_quota_sources_and_jobs(self):
        _finished_manual(self.org, 1)
        res = self.client.get(reverse("sync:history"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["quota"]["used"], 1)
        self.assertTrue(any(s["key"] == "cpe" for s in res.data["sources"]))
        self.assertEqual(len(res.data["jobs"]), 1)
        self.assertTrue(res.data["jobs"][0]["is_manual"])

    def test_start_over_limit_returns_402(self):
        _finished_manual(self.org, 2)
        res = self.client.post(reverse("sync:start"), {}, format="json")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["code"], "sync_charge_required")

    def test_start_with_acceptance_charges_and_starts(self):
        _finished_manual(self.org, 2)
        res = self.client.post(reverse("sync:start"), {"accept_charge": True}, format="json")
        self.assertEqual(res.status_code, 202)
        self.assertTrue(res.data["charged"])
        self.assertEqual(UsageCharge.objects.filter(organization=self.org).count(), 1)

    def test_charges_endpoint_lists_them(self):
        _finished_manual(self.org, 2)
        self.client.post(reverse("sync:start"), {"accept_charge": True}, format="json")
        res = self.client.get(reverse("billing:charges"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["kind"], "extra_manual_sync")
