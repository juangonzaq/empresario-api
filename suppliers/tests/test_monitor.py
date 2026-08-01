"""Tests for the daily supplier monitor."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from django.test import TestCase

from suppliers.models import Supplier, SupplierCheck
from suppliers.services.monitor import SupplierMonitor
from suppliers.services.ruc_client import RucLookupError, TaxpayerProfile

from .factories import RUC_ACTIVE, create_supplier


def profile(status: str = "ACTIVO", condition: str = "HABIDO") -> TaxpayerProfile:
    return TaxpayerProfile(
        ruc=RUC_ACTIVE,
        business_name="SUPERMERCADOS PERUANOS",
        taxpayer_type="SOCIEDAD ANONIMA",
        status=status,
        condition=condition,
        registered_on=date(1992, 10, 9),
    )


def client_returning(*profiles) -> MagicMock:
    client = MagicMock()
    client.fetch.side_effect = list(profiles)
    return client


class MonitorTests(TestCase):
    def setUp(self):
        self.supplier = create_supplier()

    def test_records_a_check_and_mirrors_it_onto_the_supplier(self):
        result = SupplierMonitor(client_returning(profile())).run()

        self.assertEqual(result.checked, 1)
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.status, "ACTIVO")
        self.assertEqual(self.supplier.condition, "HABIDO")
        self.assertEqual(self.supplier.business_name, "SUPERMERCADOS PERUANOS")
        self.assertFalse(self.supplier.has_issue)
        self.assertIsNotNone(self.supplier.last_checked_at)

        check = SupplierCheck.objects.get()
        self.assertTrue(check.succeeded)
        self.assertFalse(check.changed)

    def test_flags_a_deregistered_supplier(self):
        result = SupplierMonitor(client_returning(profile(status="BAJA DE OFICIO"))).run()

        self.assertEqual(result.with_issues, 1)
        self.supplier.refresh_from_db()
        self.assertTrue(self.supplier.has_issue)

    def test_detects_a_change_between_days(self):
        monitor = SupplierMonitor(
            client_returning(profile(), profile(status="BAJA DE OFICIO"))
        )
        monitor.run(on_date=date(2026, 7, 30))
        monitor.run(on_date=date(2026, 7, 31))

        latest = SupplierCheck.objects.get(checked_on=date(2026, 7, 31))
        self.assertTrue(latest.changed)
        self.assertEqual(latest.previous_status, "ACTIVO")
        self.assertEqual(latest.status, "BAJA DE OFICIO")

        self.supplier.refresh_from_db()
        self.assertIsNotNone(self.supplier.last_changed_at)

    def test_first_check_is_not_reported_as_a_change(self):
        SupplierMonitor(client_returning(profile())).run()
        self.assertFalse(SupplierCheck.objects.get().changed)

    def test_one_check_per_supplier_per_day(self):
        monitor = SupplierMonitor(client_returning(profile(), profile()))
        monitor.run(on_date=date(2026, 7, 31))
        monitor.run(on_date=date(2026, 7, 31))
        self.assertEqual(SupplierCheck.objects.count(), 1)

    def test_skip_checked_today_avoids_a_second_lookup(self):
        client = client_returning(profile(), profile())
        monitor = SupplierMonitor(client)
        monitor.run(on_date=date(2026, 7, 31))
        monitor.run(on_date=date(2026, 7, 31), skip_checked_today=True)
        self.assertEqual(client.fetch.call_count, 1)

    def test_untracked_suppliers_are_skipped(self):
        Supplier.objects.update(is_tracked=False)
        client = client_returning(profile())
        SupplierMonitor(client).run()
        client.fetch.assert_not_called()

    def test_a_failed_lookup_is_recorded_without_losing_the_last_good_standing(self):
        monitor = SupplierMonitor(client_returning(profile()))
        monitor.run(on_date=date(2026, 7, 30))

        failing = SupplierMonitor(MagicMock(**{
            "fetch.side_effect": RucLookupError("SUNAT is down")
        }))
        result = failing.run(on_date=date(2026, 7, 31))

        self.assertEqual(result.failed, 1)
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.status, "ACTIVO")  # preserved
        self.assertIn("SUNAT is down", self.supplier.last_error)

        check = SupplierCheck.objects.get(checked_on=date(2026, 7, 31))
        self.assertFalse(check.succeeded)

    def test_a_failed_day_does_not_look_like_a_change_next_time(self):
        """Comparing against the mirrored state, not the previous row, avoids this."""
        monitor = SupplierMonitor(client_returning(profile()))
        monitor.run(on_date=date(2026, 7, 29))
        SupplierMonitor(MagicMock(**{"fetch.side_effect": RucLookupError("down")})).run(
            on_date=date(2026, 7, 30)
        )
        SupplierMonitor(client_returning(profile())).run(on_date=date(2026, 7, 31))

        self.assertFalse(SupplierCheck.objects.get(checked_on=date(2026, 7, 31)).changed)

    def test_one_broken_supplier_does_not_stop_the_run(self):
        create_supplier(ruc="20604442533", alias="Pattern")
        client = MagicMock()
        client.fetch.side_effect = [RucLookupError("boom"), profile()]

        result = SupplierMonitor(client).run()
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.checked, 1)
