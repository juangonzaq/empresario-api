"""Tests for recording RUC profile snapshots."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

from django.test import TestCase

from ruc_profile.models import LegalRepresentative, RucSnapshot, WorkerHeadcount
from ruc_profile.services.client import FullProfile
from ruc_profile.services.constants import SECTIONS_BY_KEY
from ruc_profile.services.parsers import parse_section
from ruc_profile.services.sync import DEFAULT_MAX_AGE_DAYS, RucProfileSynchronizer
from suppliers.services.ruc_client import RucLookupError, TaxpayerProfile

from . import factories as f

TODAY = date(2026, 7, 31)


def taxpayer(status: str = "ACTIVO", condition: str = "HABIDO") -> TaxpayerProfile:
    return TaxpayerProfile(
        ruc=f.RUC, business_name=f.NAME, taxpayer_type="SOCIEDAD ANONIMA CERRADA",
        status=status, condition=condition,
    )


def full_profile(*, coactive: bool = False, reactiva: str = "NO", **kw) -> FullProfile:
    pages = {
        "historical": f.historical_page(),
        "coactive_debt": f.coactive_debt_page() if coactive else f.no_data_text_page(),
        "tax_omissions": f.no_data_text_page(),
        "workers": f.workers_page(),
        "probatory_acts": f.empty_table_page(),
        "physical_invoices": f.empty_table_page(),
        "reactiva_peru": f.boolean_page(reactiva),
        "covid_guarantee": f.boolean_page("NO"),
        "legal_representatives": f.representatives_page(),
    }
    return FullProfile(
        taxpayer=taxpayer(**kw),
        sections={k: parse_section(SECTIONS_BY_KEY[k], v) for k, v in pages.items()},
    )


def client_yielding(*profiles) -> MagicMock:
    client = MagicMock()
    client.fetch_full_profile.side_effect = list(profiles)
    return client


class CaptureTests(TestCase):
    def test_stores_the_main_table_and_every_section(self):
        result = RucProfileSynchronizer(client_yielding(full_profile())).run(
            [f.RUC], on_date=TODAY
        )
        self.assertEqual(result.captured, 1)

        snapshot = RucSnapshot.objects.get()
        self.assertEqual(snapshot.business_name, f.NAME)
        self.assertEqual(snapshot.status, "ACTIVO")
        self.assertEqual(snapshot.sections.count(), 9)

    def test_extracts_headcounts_and_representatives(self):
        RucProfileSynchronizer(client_yielding(full_profile())).run(
            [f.RUC], on_date=TODAY
        )
        self.assertEqual(WorkerHeadcount.objects.count(), 3)
        self.assertEqual(LegalRepresentative.objects.count(), 1)

        snapshot = RucSnapshot.objects.get()
        self.assertEqual(snapshot.worker_count, 5)          # newest period
        self.assertEqual(snapshot.latest_worker_period, "2026-05")

    def test_a_clean_taxpayer_has_no_risk_signals(self):
        RucProfileSynchronizer(client_yielding(full_profile())).run(
            [f.RUC], on_date=TODAY
        )
        snapshot = RucSnapshot.objects.get()
        self.assertFalse(snapshot.has_risk_signals)
        self.assertFalse(snapshot.has_coactive_debt)
        self.assertFalse(snapshot.reactiva_peru_debt)

    def test_coactive_debt_raises_a_risk_signal(self):
        result = RucProfileSynchronizer(
            client_yielding(full_profile(coactive=True))
        ).run([f.RUC], on_date=TODAY)

        self.assertEqual(result.with_risk, 1)
        snapshot = RucSnapshot.objects.get()
        self.assertTrue(snapshot.has_coactive_debt)
        self.assertTrue(snapshot.has_risk_signals)

    def test_a_yes_on_reactiva_raises_a_risk_signal(self):
        RucProfileSynchronizer(client_yielding(full_profile(reactiva="SI"))).run(
            [f.RUC], on_date=TODAY
        )
        snapshot = RucSnapshot.objects.get()
        self.assertTrue(snapshot.reactiva_peru_debt)
        self.assertTrue(snapshot.has_risk_signals)

    def test_one_snapshot_per_ruc_per_day(self):
        sync = RucProfileSynchronizer(client_yielding(full_profile(), full_profile()))
        sync.run([f.RUC], on_date=TODAY, max_age_days=None)
        sync.run([f.RUC], on_date=TODAY, max_age_days=None)
        self.assertEqual(RucSnapshot.objects.count(), 1)
        self.assertEqual(WorkerHeadcount.objects.count(), 3)  # replaced, not doubled

    def test_a_failed_capture_is_recorded(self):
        client = MagicMock()
        client.fetch_full_profile.side_effect = RucLookupError("SUNAT down")

        result = RucProfileSynchronizer(client).run([f.RUC], on_date=TODAY)

        self.assertEqual(result.failed, 1)
        snapshot = RucSnapshot.objects.get()
        self.assertFalse(snapshot.succeeded)
        self.assertIn("SUNAT down", snapshot.error)

    def test_a_failing_section_does_not_lose_the_snapshot(self):
        profile = full_profile()
        del profile.sections["physical_invoices"]
        profile.errors["physical_invoices"] = "timeout"

        RucProfileSynchronizer(client_yielding(profile)).run([f.RUC], on_date=TODAY)

        snapshot = RucSnapshot.objects.get()
        self.assertTrue(snapshot.succeeded)
        failed = snapshot.sections.get(key="physical_invoices")
        self.assertEqual(failed.error, "timeout")


class ChangeDetectionTests(TestCase):
    def capture(self, profile, on_date):
        RucProfileSynchronizer(client_yielding(profile)).run(
            [f.RUC], on_date=on_date, max_age_days=None
        )

    def test_first_capture_is_not_a_change(self):
        self.capture(full_profile(), TODAY)
        self.assertFalse(RucSnapshot.objects.get().changed)

    def test_status_change_is_reported(self):
        self.capture(full_profile(), TODAY - timedelta(days=40))
        self.capture(full_profile(status="BAJA DE OFICIO"), TODAY)

        snapshot = RucSnapshot.objects.get(captured_on=TODAY)
        self.assertTrue(snapshot.changed)
        self.assertIn("BAJA DE OFICIO", snapshot.change_summary)

    def test_a_new_risk_signal_is_reported(self):
        self.capture(full_profile(), TODAY - timedelta(days=40))
        self.capture(full_profile(coactive=True), TODAY)

        snapshot = RucSnapshot.objects.get(captured_on=TODAY)
        self.assertTrue(snapshot.changed)
        self.assertIn("has_coactive_debt", snapshot.change_summary)

    def test_an_identical_capture_is_not_a_change(self):
        self.capture(full_profile(), TODAY - timedelta(days=40))
        self.capture(full_profile(), TODAY)
        self.assertFalse(RucSnapshot.objects.get(captured_on=TODAY).changed)


class FreshnessTests(TestCase):
    def setUp(self):
        RucProfileSynchronizer(client_yielding(full_profile())).run(
            [f.RUC], on_date=TODAY - timedelta(days=5), max_age_days=None
        )

    def test_a_recent_snapshot_is_reused(self):
        client = client_yielding(full_profile())
        result = RucProfileSynchronizer(client).run([f.RUC], on_date=TODAY)

        client.fetch_full_profile.assert_not_called()
        self.assertEqual(result.skipped, 1)

    def test_a_stale_snapshot_is_recaptured(self):
        client = client_yielding(full_profile())
        stale = TODAY + timedelta(days=DEFAULT_MAX_AGE_DAYS + 1)
        RucProfileSynchronizer(client).run([f.RUC], on_date=stale)
        client.fetch_full_profile.assert_called_once()

    def test_force_ignores_freshness(self):
        client = client_yielding(full_profile())
        RucProfileSynchronizer(client).run([f.RUC], on_date=TODAY, max_age_days=None)
        client.fetch_full_profile.assert_called_once()
