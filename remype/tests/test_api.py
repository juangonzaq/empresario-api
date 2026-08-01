"""Tests for the REMYPE API."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from django.test import override_settings
from django.urls import reverse
from rest_framework import status as http
from rest_framework.test import APITestCase

from remype.models import RemypeCheck
from remype.services.sync import RemypeSynchronizer

from .factories import RUC_REGISTERED, RUC_UNREGISTERED
from .test_sync import TODAY, accredited, client_yielding


def make_check(ruc: str, checked_on: date, **overrides) -> RemypeCheck:
    defaults = {
        "ruc": ruc, "checked_on": checked_on, "is_registered": True,
        "business_name": "PATTERN GROUP S.A.C.",
        "condition": "ACREDITADO COMO MICRO EMPRESA",
        "accredited_on": date(2023, 6, 16), "succeeded": True,
    }
    return RemypeCheck.objects.create(**{**defaults, **overrides})


class RemypeReadTests(APITestCase):
    def setUp(self):
        self.old = make_check(RUC_REGISTERED, TODAY - timedelta(days=40))
        self.new = make_check(RUC_REGISTERED, TODAY)
        self.other = make_check(
            RUC_UNREGISTERED, TODAY, is_registered=False, condition="",
            business_name="", accredited_on=None,
        )
        self.list_url = reverse("remype:remype-list")

    def test_lists_every_check(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.data["count"], 3)

    def test_current_returns_one_row_per_ruc(self):
        response = self.client.get(reverse("remype:remype-current"))
        self.assertEqual(response.data["count"], 2)

    def test_current_for_a_single_ruc_returns_the_newest(self):
        url = reverse("remype:remype-current")
        response = self.client.get(url, {"ruc": RUC_REGISTERED})
        self.assertEqual(response.status_code, http.HTTP_200_OK)
        self.assertEqual(response.data["checked_on"], str(TODAY))
        self.assertTrue(response.data["is_active"])

    def test_current_404s_for_an_unknown_ruc(self):
        response = self.client.get(reverse("remype:remype-current"), {"ruc": "20131312955"})
        self.assertEqual(response.status_code, http.HTTP_404_NOT_FOUND)

    def test_filters_by_registration(self):
        response = self.client.get(self.list_url, {"is_registered": "false"})
        self.assertEqual(response.data["count"], 1)

    def test_filters_by_is_active(self):
        make_check(
            "20131312955", TODAY, deregistered_on=date(2025, 3, 1)
        )
        response = self.client.get(self.list_url, {"is_active": "true"})
        self.assertEqual(response.data["count"], 2)

    def test_history_is_read_only(self):
        response = self.client.post(self.list_url, {"ruc": RUC_REGISTERED})
        self.assertEqual(response.status_code, http.HTTP_405_METHOD_NOT_ALLOWED)

    @override_settings(SUNAT_RUC=RUC_REGISTERED)
    def test_me_returns_the_configured_company(self):
        response = self.client.get(reverse("remype:remype-me"))
        self.assertEqual(response.status_code, http.HTTP_200_OK)
        self.assertEqual(response.data["ruc"], RUC_REGISTERED)

    @override_settings(SUNAT_RUC="")
    def test_me_requires_the_setting(self):
        response = self.client.get(reverse("remype:remype-me"))
        self.assertEqual(response.status_code, http.HTTP_400_BAD_REQUEST)


class RemypeLookupTests(APITestCase):
    def setUp(self):
        self.url = reverse("remype:remype-lookup")

    def test_rejects_an_invalid_ruc(self):
        response = self.client.post(self.url, {"ruc": "12345678901"})
        self.assertEqual(response.status_code, http.HTTP_400_BAD_REQUEST)
        self.assertIn("ruc", response.data)

    def test_runs_a_lookup_and_returns_the_check(self):
        sync = RemypeSynchronizer(client_yielding(accredited()))
        with patch("remype.views.RemypeSynchronizer", return_value=sync):
            response = self.client.post(self.url, {"ruc": RUC_REGISTERED})

        self.assertEqual(response.status_code, http.HTTP_200_OK)
        self.assertTrue(response.data["check"]["is_registered"])
        self.assertFalse(response.data["reused_cached_check"])

    def test_reuses_a_recent_check_without_querying_remype(self):
        make_check(RUC_REGISTERED, date.today())
        client = MagicMock()
        with patch("remype.views.RemypeSynchronizer",
                   return_value=RemypeSynchronizer(client)):
            response = self.client.post(self.url, {"ruc": RUC_REGISTERED})

        client.fetch.assert_not_called()
        self.assertTrue(response.data["reused_cached_check"])

    def test_force_bypasses_the_cache(self):
        make_check(RUC_REGISTERED, date.today(), condition="OLD")
        sync = RemypeSynchronizer(client_yielding(accredited()))
        with patch("remype.views.RemypeSynchronizer", return_value=sync):
            response = self.client.post(
                self.url, {"ruc": RUC_REGISTERED, "force": True}
            )

        self.assertFalse(response.data["reused_cached_check"])
        self.assertEqual(
            response.data["check"]["condition"], "ACREDITADO COMO MICRO EMPRESA"
        )
