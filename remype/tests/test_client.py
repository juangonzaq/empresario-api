"""Tests for parsing REMYPE responses. No browser is started."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from remype.services.client import RemypeClient, RemypeLookupError, RemypeProfile

from .factories import (
    RUC_REGISTERED,
    RUC_UNREGISTERED,
    captcha_rejected_response,
    found_response,
    not_found_response,
)


def client_returning(response) -> RemypeClient:
    client = RemypeClient()
    client._page = MagicMock()
    client._page.evaluate.return_value = response
    return client


class ParseTests(SimpleTestCase):
    def test_parses_an_accredited_company(self):
        profile = client_returning(found_response()).fetch(RUC_REGISTERED)

        self.assertTrue(profile.is_registered)
        self.assertTrue(profile.is_active)
        self.assertEqual(profile.business_name, "PATTERN GROUP S.A.C.")
        self.assertEqual(profile.condition, "ACREDITADO COMO MICRO EMPRESA")
        self.assertEqual(profile.accredited_on, date(2023, 6, 16))
        self.assertEqual(profile.requested_on, date(2023, 6, 14))
        self.assertEqual(profile.registry_code, 678874)

    def test_strips_the_padding_remype_adds(self):
        profile = client_returning(found_response()).fetch(RUC_REGISTERED)
        self.assertEqual(profile.situation, "ACREDITADO")

    def test_dashed_placeholder_becomes_none(self):
        """FECHABAJA arrives as '   --- --- ---   ' when the company is not struck off."""
        profile = client_returning(found_response()).fetch(RUC_REGISTERED)
        self.assertIsNone(profile.deregistered_on)

    def test_a_struck_off_company_is_registered_but_not_active(self):
        profile = client_returning(
            found_response(FECHABAJA="01/03/2025")
        ).fetch(RUC_REGISTERED)

        self.assertTrue(profile.is_registered)
        self.assertFalse(profile.is_active)
        self.assertEqual(profile.deregistered_on, date(2025, 3, 1))

    def test_unregistered_ruc_returns_an_empty_profile(self):
        profile = client_returning(not_found_response()).fetch(RUC_UNREGISTERED)

        self.assertFalse(profile.is_registered)
        self.assertFalse(profile.is_active)
        self.assertIn("No se tiene información", profile.message)

    def test_rejected_captcha_raises(self):
        with self.assertRaises(RemypeLookupError) as ctx:
            client_returning(captcha_rejected_response()).fetch(RUC_REGISTERED)
        self.assertIn("401", str(ctx.exception))

    def test_invalid_json_raises(self):
        response = {"status": 200, "body": "<html>gateway error</html>"}
        with self.assertRaises(RemypeLookupError):
            client_returning(response).fetch(RUC_REGISTERED)

    def test_empty_data_list_is_treated_as_unregistered(self):
        response = {"status": 200, "body": '{"status":"0","data":[],"message":null}'}
        profile = client_returning(response).fetch(RUC_UNREGISTERED)
        self.assertFalse(profile.is_registered)


class ProfileTests(SimpleTestCase):
    def test_unregistered_is_never_active(self):
        profile = RemypeProfile(ruc=RUC_UNREGISTERED, is_registered=False)
        self.assertFalse(profile.is_active)
