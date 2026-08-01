"""Tests for the Celery tasks. Tasks are called directly: no broker involved."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import TestCase
from django_celery_beat.models import PeriodicTask

from suppliers.models import Supplier, SupplierCheck
from suppliers.services.monitor import SupplierMonitor
from suppliers.services.ruc_client import RucLookupError
from suppliers.tasks import check_all_suppliers, check_supplier

from .factories import RUC_ACTIVE, create_supplier
from .test_monitor import profile


def monitor_with(*profiles) -> SupplierMonitor:
    client = MagicMock()
    client.fetch.side_effect = list(profiles)
    return SupplierMonitor(client)


class CheckAllSuppliersTaskTests(TestCase):
    def setUp(self):
        self.supplier = create_supplier()

    def test_returns_a_json_serialisable_summary(self):
        with patch("suppliers.tasks.SupplierMonitor", return_value=monitor_with(profile())):
            result = check_all_suppliers(skip_checked_today=False)

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["flagged"], [])

    def test_reports_flagged_suppliers(self):
        monitor = monitor_with(profile(status="BAJA DE OFICIO"))
        with patch("suppliers.tasks.SupplierMonitor", return_value=monitor):
            result = check_all_suppliers(skip_checked_today=False)

        self.assertEqual(result["with_issues"], 1)
        self.assertEqual(result["flagged"], [RUC_ACTIVE])

    def test_skips_suppliers_already_checked_today_by_default(self):
        """A retry after a partial failure must not hammer SUNAT again."""
        first = monitor_with(profile())
        with patch("suppliers.tasks.SupplierMonitor", return_value=first):
            check_all_suppliers(skip_checked_today=False)

        second = monitor_with(profile())
        with patch("suppliers.tasks.SupplierMonitor", return_value=second):
            result = check_all_suppliers()

        self.assertEqual(result["checked"], 0)
        self.assertEqual(SupplierCheck.objects.count(), 1)


class CheckSupplierTaskTests(TestCase):
    def setUp(self):
        self.supplier = create_supplier()

    def test_checks_a_single_supplier(self):
        with patch("suppliers.tasks.SupplierMonitor", return_value=monitor_with(profile())):
            result = check_supplier(RUC_ACTIVE)

        self.assertEqual(result["ruc"], RUC_ACTIVE)
        self.assertEqual(result["status"], "ACTIVO")
        self.assertTrue(result["succeeded"])

    def test_unknown_ruc_raises(self):
        with self.assertRaises(Supplier.DoesNotExist):
            check_supplier("20131312955")

    def test_is_configured_to_retry_on_lookup_errors(self):
        self.assertIn(RucLookupError, check_supplier.autoretry_for)


class BeatScheduleTests(TestCase):
    """The schedule is seeded by a data migration, so it must exist on a fresh DB."""

    def test_daily_supplier_check_is_scheduled(self):
        task = PeriodicTask.objects.get(task="suppliers.check_all")
        self.assertTrue(task.enabled)
        self.assertEqual(task.crontab.hour, "7")
        self.assertEqual(task.crontab.minute, "0")

    def test_mailbox_scrape_is_scheduled_without_overlapping(self):
        suppliers_task = PeriodicTask.objects.get(task="suppliers.check_all")
        mailbox_task = PeriodicTask.objects.get(task="sunat_mailbox.scrape")
        self.assertTrue(mailbox_task.enabled)
        self.assertNotEqual(
            (suppliers_task.crontab.hour, suppliers_task.crontab.minute),
            (mailbox_task.crontab.hour, mailbox_task.crontab.minute),
        )
