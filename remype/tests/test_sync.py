"""Tests for recording REMYPE lookups and the caching policy."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

from django.test import TestCase

from remype.models import RemypeCheck
from remype.services.client import RemypeLookupError, RemypeProfile
from remype.services.sync import DEFAULT_MAX_AGE_DAYS, RemypeSynchronizer

from .factories import RUC_REGISTERED, RUC_UNREGISTERED

TODAY = date(2026, 7, 31)


def accredited(condition: str = "ACREDITADO COMO MICRO EMPRESA", **kwargs) -> RemypeProfile:
    return RemypeProfile(
        ruc=RUC_REGISTERED, is_registered=True,
        business_name="PATTERN GROUP S.A.C.", condition=condition,
        accredited_on=date(2023, 6, 16), **kwargs,
    )


def unregistered() -> RemypeProfile:
    return RemypeProfile(ruc=RUC_UNREGISTERED, is_registered=False, message="none")


def client_yielding(*profiles) -> MagicMock:
    client = MagicMock()
    client.fetch.side_effect = list(profiles)
    return client


class RecordingTests(TestCase):
    def test_stores_an_accredited_company(self):
        client = client_yielding(accredited())
        result = RemypeSynchronizer(client).run([RUC_REGISTERED], on_date=TODAY)

        self.assertEqual(result.checked, 1)
        self.assertEqual(result.registered, 1)

        check = RemypeCheck.objects.get()
        self.assertTrue(check.is_registered)
        self.assertTrue(check.is_active)
        self.assertEqual(check.accredited_on, date(2023, 6, 16))

    def test_stores_an_unregistered_company(self):
        client = client_yielding(unregistered())
        result = RemypeSynchronizer(client).run([RUC_UNREGISTERED], on_date=TODAY)

        self.assertEqual(result.registered, 0)
        self.assertFalse(RemypeCheck.objects.get().is_registered)

    def test_one_check_per_ruc_per_day(self):
        client = client_yielding(accredited(), accredited())
        sync = RemypeSynchronizer(client)
        sync.run([RUC_REGISTERED], on_date=TODAY, max_age_days=None)
        sync.run([RUC_REGISTERED], on_date=TODAY, max_age_days=None)
        self.assertEqual(RemypeCheck.objects.count(), 1)

    def test_a_failed_lookup_is_recorded(self):
        client = MagicMock()
        client.fetch.side_effect = RemypeLookupError("captcha rejected")

        result = RemypeSynchronizer(client).run([RUC_REGISTERED], on_date=TODAY)

        self.assertEqual(result.failed, 1)
        check = RemypeCheck.objects.get()
        self.assertFalse(check.succeeded)
        self.assertIn("captcha", check.message)

    def test_one_failure_does_not_stop_the_batch(self):
        client = MagicMock()
        client.fetch.side_effect = [RemypeLookupError("boom"), unregistered()]

        result = RemypeSynchronizer(client).run(
            [RUC_REGISTERED, RUC_UNREGISTERED], on_date=TODAY
        )
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.checked, 1)


class ChangeDetectionTests(TestCase):
    def test_first_check_is_not_a_change(self):
        RemypeSynchronizer(client_yielding(accredited())).run(
            [RUC_REGISTERED], on_date=TODAY
        )
        self.assertFalse(RemypeCheck.objects.get().changed)

    def test_losing_accreditation_is_flagged(self):
        sync = RemypeSynchronizer(client_yielding(accredited(), unregistered()))
        sync.run([RUC_REGISTERED], on_date=TODAY - timedelta(days=60), max_age_days=None)
        sync.run([RUC_REGISTERED], on_date=TODAY, max_age_days=None)

        latest = RemypeCheck.objects.get(checked_on=TODAY)
        self.assertTrue(latest.changed)
        self.assertEqual(latest.previous_condition, "ACREDITADO COMO MICRO EMPRESA")

    def test_a_failed_day_does_not_read_as_a_change(self):
        sync = RemypeSynchronizer(client_yielding(accredited()))
        sync.run([RUC_REGISTERED], on_date=TODAY - timedelta(days=60), max_age_days=None)

        failing = MagicMock()
        failing.fetch.side_effect = RemypeLookupError("down")
        RemypeSynchronizer(failing).run(
            [RUC_REGISTERED], on_date=TODAY - timedelta(days=30), max_age_days=None
        )

        RemypeSynchronizer(client_yielding(accredited())).run(
            [RUC_REGISTERED], on_date=TODAY, max_age_days=None
        )
        self.assertFalse(RemypeCheck.objects.get(checked_on=TODAY).changed)


class CachingTests(TestCase):
    """REMYPE barely changes, so recent checks are reused instead of re-fetched."""

    def setUp(self):
        RemypeSynchronizer(client_yielding(accredited())).run(
            [RUC_REGISTERED], on_date=TODAY - timedelta(days=5), max_age_days=None
        )

    def test_a_recent_check_is_reused(self):
        client = client_yielding(accredited())
        result = RemypeSynchronizer(client).run([RUC_REGISTERED], on_date=TODAY)

        client.fetch.assert_not_called()
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.checked, 0)

    def test_a_stale_check_is_refreshed(self):
        client = client_yielding(accredited())
        stale_day = TODAY + timedelta(days=DEFAULT_MAX_AGE_DAYS + 1)
        result = RemypeSynchronizer(client).run([RUC_REGISTERED], on_date=stale_day)

        client.fetch.assert_called_once()
        self.assertEqual(result.checked, 1)

    def test_max_age_none_always_refetches(self):
        client = client_yielding(accredited())
        RemypeSynchronizer(client).run([RUC_REGISTERED], on_date=TODAY, max_age_days=None)
        client.fetch.assert_called_once()

    def test_a_failed_check_does_not_count_as_fresh(self):
        RemypeCheck.objects.update(succeeded=False)
        client = client_yielding(accredited())
        RemypeSynchronizer(client).run([RUC_REGISTERED], on_date=TODAY)
        client.fetch.assert_called_once()

    def test_no_browser_is_started_when_everything_is_fresh(self):
        """The synchronizer must not pay for a browser it does not need."""
        with self.assertNumQueries(1):
            result = RemypeSynchronizer().run([RUC_REGISTERED], on_date=TODAY)
        self.assertEqual(result.skipped, 1)
