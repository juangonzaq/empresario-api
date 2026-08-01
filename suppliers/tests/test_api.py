"""Tests for the suppliers API."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from django.urls import reverse
from rest_framework import status as http
from rest_framework.test import APITestCase

from suppliers.models import Supplier, SupplierCheck

from .factories import RUC_ACTIVE, RUC_OTHER, create_supplier
from .test_monitor import profile


class SupplierAPITests(APITestCase):
    def setUp(self):
        self.healthy = create_supplier(ruc=RUC_ACTIVE, alias="Supermercados")
        self.healthy.status, self.healthy.condition = "ACTIVO", "HABIDO"
        self.healthy.save()

        self.flagged = create_supplier(ruc=RUC_OTHER, alias="Pattern")
        self.flagged.status = "BAJA DE OFICIO"
        self.flagged.condition = "NO HABIDO"
        self.flagged.has_issue = True
        self.flagged.save()

        self.list_url = reverse("suppliers:supplier-list")

    def test_registers_a_supplier(self):
        response = self.client.post(
            self.list_url, {"ruc": "20131312955", "alias": "Nuevo"}
        )
        self.assertEqual(response.status_code, http.HTTP_201_CREATED)
        self.assertTrue(Supplier.objects.filter(ruc="20131312955").exists())

    def test_rejects_an_invalid_ruc(self):
        response = self.client.post(self.list_url, {"ruc": "12345678901"})
        self.assertEqual(response.status_code, http.HTTP_400_BAD_REQUEST)
        self.assertIn("ruc", response.data)

    def test_rejects_a_duplicate_ruc(self):
        response = self.client.post(self.list_url, {"ruc": RUC_ACTIVE})
        self.assertEqual(response.status_code, http.HTTP_400_BAD_REQUEST)

    def test_sunat_fields_are_read_only(self):
        url = reverse("suppliers:supplier-detail", args=[self.healthy.pk])
        self.client.patch(url, {"status": "TAMPERED", "alias": "Renamed"}, format="json")
        self.healthy.refresh_from_db()
        self.assertEqual(self.healthy.status, "ACTIVO")
        self.assertEqual(self.healthy.alias, "Renamed")

    def test_filters_by_issue(self):
        response = self.client.get(self.list_url, {"has_issue": "true"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["ruc"], RUC_OTHER)

    def test_filters_never_checked(self):
        response = self.client.get(self.list_url, {"never_checked": "true"})
        self.assertEqual(response.data["count"], 2)

    def test_search_by_alias(self):
        response = self.client.get(self.list_url, {"search": "Pattern"})
        self.assertEqual(response.data["count"], 1)

    def test_summary_reports_flagged_suppliers(self):
        response = self.client.get(reverse("suppliers:supplier-summary"))
        self.assertEqual(response.data["total"], 2)
        self.assertEqual(response.data["with_issues"], 1)
        self.assertEqual(response.data["never_checked"], 2)
        self.assertEqual(len(response.data["flagged"]), 1)
        self.assertEqual(response.data["flagged"][0]["ruc"], RUC_OTHER)

    def test_detail_includes_recent_checks(self):
        SupplierCheck.objects.create(
            supplier=self.healthy, checked_on=date(2026, 7, 31),
            status="ACTIVO", condition="HABIDO",
        )
        url = reverse("suppliers:supplier-detail", args=[self.healthy.pk])
        response = self.client.get(url)
        self.assertEqual(len(response.data["latest_checks"]), 1)

    def test_checks_endpoint_lists_history(self):
        for day in (30, 31):
            SupplierCheck.objects.create(
                supplier=self.healthy, checked_on=date(2026, 7, day),
                status="ACTIVO", condition="HABIDO",
            )
        url = reverse("suppliers:supplier-checks", args=[self.healthy.pk])
        response = self.client.get(url)
        self.assertEqual(response.data["count"], 2)

    def test_check_action_runs_a_lookup_now(self):
        url = reverse("suppliers:supplier-check", args=[self.healthy.pk])
        fake = MagicMock()
        fake.fetch.return_value = profile(status="BAJA DE OFICIO")
        with patch("suppliers.views.SupplierMonitor") as monitor_class:
            from suppliers.services.monitor import SupplierMonitor
            monitor_class.return_value = SupplierMonitor(fake)
            response = self.client.post(url)

        self.assertEqual(response.status_code, http.HTTP_200_OK)
        self.assertEqual(response.data["check"]["status"], "BAJA DE OFICIO")
        self.healthy.refresh_from_db()
        self.assertTrue(self.healthy.has_issue)


class SupplierCheckAPITests(APITestCase):
    def setUp(self):
        self.supplier = create_supplier()
        for day, state in ((29, "ACTIVO"), (30, "ACTIVO"), (31, "BAJA DE OFICIO")):
            SupplierCheck.objects.create(
                supplier=self.supplier, checked_on=date(2026, 7, day),
                status=state, condition="HABIDO",
                has_issue=state != "ACTIVO", changed=day == 31,
            )
        self.url = reverse("suppliers:suppliercheck-list")

    def test_lists_all_checks_newest_first(self):
        response = self.client.get(self.url)
        self.assertEqual(response.data["count"], 3)
        self.assertEqual(response.data["results"][0]["checked_on"], "2026-07-31")

    def test_filters_by_ruc_and_change(self):
        response = self.client.get(self.url, {"ruc": self.supplier.ruc, "changed": "true"})
        self.assertEqual(response.data["count"], 1)

    def test_filters_by_date_range(self):
        response = self.client.get(self.url, {"date_from": "2026-07-30"})
        self.assertEqual(response.data["count"], 2)

    def test_history_is_read_only(self):
        response = self.client.post(self.url, {"status": "nope"})
        self.assertEqual(response.status_code, http.HTTP_405_METHOD_NOT_ALLOWED)
