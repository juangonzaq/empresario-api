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

    def test_unknown_ruc_reports_instead_of_raising(self):
        # Reventar haría que Celery reintente tres veces algo que nunca va a
        # existir; se informa y se acaba.
        result = check_supplier("20131312955")
        self.assertFalse(result["succeeded"])
        self.assertIn("no registrado", result["error"])

    def test_the_same_ruc_in_two_companies_is_looked_up_once(self):
        """El estado en SUNAT es público y único: una consulta, dos fichas."""
        create_supplier(account_ruc="20999999999", ruc=RUC_ACTIVE, alias="Otra cartera")
        monitor = monitor_with(profile())
        with patch("suppliers.tasks.SupplierMonitor", return_value=monitor):
            result = check_supplier(RUC_ACTIVE)

        self.assertEqual(result["fichas"], 2)
        self.assertTrue(result["succeeded"])

    def test_is_configured_to_retry_on_lookup_errors(self):
        self.assertIn(RucLookupError, check_supplier.autoretry_for)


class BeatScheduleTests(TestCase):
    """The schedule is seeded by a data migration, so it must exist on a fresh DB."""

    def test_daily_supplier_check_is_scheduled(self):
        task = PeriodicTask.objects.get(task="suppliers.check_all")
        self.assertTrue(task.enabled)
        self.assertEqual(task.crontab.hour, "7")
        self.assertEqual(task.crontab.minute, "0")

    def test_tenant_syncs_are_scheduled_and_do_not_overlap(self):
        """Los horarios por fuente se retiraron: ahora Beat recorre empresas."""
        suppliers_task = PeriodicTask.objects.get(task="suppliers.check_all")
        daily = PeriodicTask.objects.get(task="sync.daily")
        monthly = PeriodicTask.objects.get(task="sync.monthly")
        self.assertTrue(daily.enabled)
        self.assertTrue(monthly.enabled)

        horas = {
            (t.crontab.hour, t.crontab.minute)
            for t in (suppliers_task, daily, monthly)
        }
        self.assertEqual(len(horas), 3, "dos tareas arrancan a la misma hora")

    def test_single_tenant_schedules_are_gone(self):
        # Cada uno de estos leía settings.SUNAT_RUC: con varias empresas
        # sincronizaba a una sola.
        for task in (
            "sunat_cpe.scrape_daily", "sunat_itf.scrape", "sunat_mailbox.scrape",
            "sunafil.scrape", "ruc_profile.capture", "remype.refresh",
        ):
            self.assertFalse(
                PeriodicTask.objects.filter(task=task).exists(),
                f"{task} sigue programado para una sola empresa",
            )
