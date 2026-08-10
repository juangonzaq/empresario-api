"""Tests for the RUC profile API."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from django.test import override_settings
from django.urls import reverse
from rest_framework import status as http
from rest_framework.test import APITestCase  # noqa: F401

from core.testing import TenantAPITestCase

from ruc_profile.models import RucSection, RucSnapshot, WorkerHeadcount
from ruc_profile.services.sync import RucProfileSynchronizer

from . import factories as f
from .test_sync import TODAY, client_yielding, full_profile

OTHER_RUC = "20100070970"


def make_snapshot(ruc: str = f.RUC, captured_on: date = TODAY, **overrides) -> RucSnapshot:
    defaults = {
        "ruc": ruc, "captured_on": captured_on, "business_name": f.NAME,
        "status": "ACTIVO", "condition": "HABIDO", "succeeded": True,
    }
    return RucSnapshot.objects.create(**{**defaults, **overrides})


class ProfileReadTests(TenantAPITestCase):
    def setUp(self):
        self.old = make_snapshot(captured_on=TODAY - timedelta(days=40))
        self.current = make_snapshot(worker_count=5, latest_worker_period="2026-05")
        self.flagged = make_snapshot(
            ruc=OTHER_RUC, business_name="OTRA S.A.C.",
            has_coactive_debt=True, has_risk_signals=True,
        )
        self.list_url = reverse("ruc_profile:rucprofile-list")

    def test_lists_every_snapshot(self):
        self.assertEqual(self.client.get(self.list_url).data["count"], 3)

    def test_current_returns_one_per_ruc(self):
        response = self.client.get(reverse("ruc_profile:rucprofile-current"))
        self.assertEqual(response.data["count"], 2)

    def test_current_for_one_ruc_returns_the_newest_in_full(self):
        RucSection.objects.create(
            snapshot=self.current, key="workers", label="Trabajadores", has_data=True
        )
        WorkerHeadcount.objects.create(
            snapshot=self.current, period="2026-05", workers=5
        )
        response = self.client.get(
            reverse("ruc_profile:rucprofile-current"), {"ruc": f.RUC}
        )
        self.assertEqual(response.status_code, http.HTTP_200_OK)
        self.assertEqual(response.data["captured_on"], str(TODAY))
        self.assertEqual(len(response.data["sections"]), 1)
        self.assertEqual(len(response.data["headcounts"]), 1)

    def test_current_404s_for_an_unknown_ruc(self):
        response = self.client.get(
            reverse("ruc_profile:rucprofile-current"), {"ruc": "20131312955"}
        )
        self.assertEqual(response.status_code, http.HTTP_404_NOT_FOUND)

    def test_filters_by_risk(self):
        response = self.client.get(self.list_url, {"has_risk_signals": "true"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["ruc"], OTHER_RUC)

    def test_filters_by_coactive_debt(self):
        response = self.client.get(self.list_url, {"has_coactive_debt": "true"})
        self.assertEqual(response.data["count"], 1)

    def test_list_omits_the_heavy_sections(self):
        result = self.client.get(self.list_url).data["results"][0]
        self.assertNotIn("sections", result)

    def test_is_read_only(self):
        response = self.client.post(self.list_url, {"ruc": f.RUC})
        self.assertEqual(response.status_code, http.HTTP_405_METHOD_NOT_ALLOWED)

    def test_me_returns_the_active_company(self):
        response = self.client.get(reverse("ruc_profile:rucprofile-me"))
        self.assertEqual(response.status_code, http.HTTP_200_OK)
        self.assertEqual(response.data["ruc"], f.RUC)
        self.assertIn("sections", response.data)

    @override_settings(SUNAT_RUC="")
    def test_me_no_depende_de_la_variable_de_entorno(self):
        """Salía de ``settings.SUNAT_RUC`` —un solo RUC para toda la
        instalación—, así que en una cuenta con varias empresas respondía
        siempre por la misma, y por la equivocada."""
        response = self.client.get(reverse("ruc_profile:rucprofile-me"))
        self.assertEqual(response.status_code, http.HTTP_200_OK)
        self.assertEqual(response.data["ruc"], f.RUC)


class CaptureEndpointTests(TenantAPITestCase):
    def setUp(self):
        self.url = reverse("ruc_profile:rucprofile-capture")

    def test_rejects_an_invalid_ruc(self):
        response = self.client.post(self.url, {"ruc": "12345678901"})
        self.assertEqual(response.status_code, http.HTTP_400_BAD_REQUEST)

    def test_captures_and_returns_the_snapshot(self):
        sync = RucProfileSynchronizer(client_yielding(full_profile()))
        with patch("ruc_profile.views.RucProfileSynchronizer", return_value=sync):
            response = self.client.post(self.url, {"ruc": f.RUC})

        self.assertEqual(response.status_code, http.HTTP_200_OK)
        self.assertFalse(response.data["reused_recent_snapshot"])
        self.assertEqual(response.data["snapshot"]["business_name"], f.NAME)
        self.assertEqual(len(response.data["snapshot"]["sections"]), 9)

    def test_reuses_a_recent_snapshot(self):
        make_snapshot(captured_on=date.today())
        client = MagicMock()
        with patch("ruc_profile.views.RucProfileSynchronizer",
                   return_value=RucProfileSynchronizer(client)):
            response = self.client.post(self.url, {"ruc": f.RUC})

        client.fetch_full_profile.assert_not_called()
        self.assertTrue(response.data["reused_recent_snapshot"])

    def test_force_bypasses_the_cache(self):
        make_snapshot(captured_on=date.today(), status="OLD")
        sync = RucProfileSynchronizer(client_yielding(full_profile()))
        with patch("ruc_profile.views.RucProfileSynchronizer", return_value=sync):
            response = self.client.post(self.url, {"ruc": f.RUC, "force": True})

        self.assertFalse(response.data["reused_recent_snapshot"])
        self.assertEqual(response.data["snapshot"]["status"], "ACTIVO")
