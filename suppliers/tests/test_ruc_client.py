"""Tests for RUC validation and the SUNAT lookup parser."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import requests
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from suppliers.services.ruc_client import (
    RucLookupClient,
    RucLookupError,
    RucNotFoundError,
    TaxpayerProfile,
)
from suppliers.validators import is_valid_ruc, validate_ruc

from .factories import EMPTY_PAGE, INVALID_PAGE, RUC_ACTIVE, RUC_OTHER, result_page


class RucValidatorTests(SimpleTestCase):
    def test_accepts_real_rucs(self):
        for ruc in (RUC_ACTIVE, RUC_OTHER, "20131312955"):
            self.assertTrue(is_valid_ruc(ruc), ruc)

    def test_rejects_wrong_check_digit(self):
        self.assertFalse(is_valid_ruc("20100070971"))

    def test_rejects_wrong_length_and_non_digits(self):
        for value in ("2010007097", "201000709701", "abcdefghijk", ""):
            self.assertFalse(is_valid_ruc(value), value)

    def test_validator_raises_for_invalid_input(self):
        with self.assertRaises(ValidationError):
            validate_ruc("12345678901")


class ProfileTests(SimpleTestCase):
    def test_active_and_found_is_healthy(self):
        profile = TaxpayerProfile(ruc=RUC_ACTIVE, status="ACTIVO", condition="HABIDO")
        self.assertFalse(profile.has_issue)

    def test_deregistered_is_an_issue(self):
        profile = TaxpayerProfile(
            ruc=RUC_ACTIVE, status="BAJA DE OFICIO", condition="HABIDO"
        )
        self.assertTrue(profile.has_issue)

    def test_not_found_condition_is_an_issue(self):
        profile = TaxpayerProfile(ruc=RUC_ACTIVE, status="ACTIVO", condition="NO HABIDO")
        self.assertTrue(profile.has_issue)

    def test_unknown_values_fail_safe_as_an_issue(self):
        """A state SUNAT invents later must be flagged, not silently accepted."""
        profile = TaxpayerProfile(
            ruc=RUC_ACTIVE, status="ALGUN ESTADO NUEVO", condition="HABIDO"
        )
        self.assertTrue(profile.has_issue)


class LookupParsingTests(SimpleTestCase):
    def fetch(self, html: str, ruc: str = RUC_ACTIVE) -> TaxpayerProfile:
        client = RucLookupClient()
        with patch.object(client, "_request", return_value=html):
            return client.fetch(ruc)

    def test_parses_the_core_fields(self):
        profile = self.fetch(result_page())
        self.assertEqual(profile.status, "ACTIVO")
        self.assertEqual(profile.condition, "HABIDO")
        self.assertEqual(profile.taxpayer_type, "SOCIEDAD ANONIMA")
        self.assertEqual(profile.trade_name, "SUPERMERCADOS PERUANOS")
        self.assertEqual(profile.registered_on, date(1992, 10, 9))

    def test_strips_quotes_from_the_business_name(self):
        profile = self.fetch(result_page())
        self.assertEqual(
            profile.business_name, "SUPERMERCADOS PERUANOS SOCIEDAD ANONIMA O S.P.S.A."
        )

    def test_reads_extra_labels_from_the_nested_table(self):
        """SUNAT repeats the table inside itself, with a few additional labels."""
        profile = self.fetch(result_page())
        self.assertEqual(profile.started_activities_on, date(1979, 6, 1))

    def test_nested_table_does_not_bleed_into_a_value(self):
        profile = self.fetch(result_page())
        self.assertEqual(profile.registries, "NINGUNO")

    def test_html_comments_are_not_treated_as_text(self):
        profile = self.fetch(result_page())
        self.assertNotIn("developer comment", profile.registries)

    def test_empty_page_raises_not_found(self):
        with self.assertRaises(RucNotFoundError):
            self.fetch(EMPTY_PAGE)

    def test_invalid_ruc_page_raises_not_found(self):
        with self.assertRaises(RucNotFoundError) as ctx:
            self.fetch(INVALID_PAGE)
        self.assertIn("invalid", str(ctx.exception).lower())

    def test_network_failure_raises_lookup_error(self):
        client = RucLookupClient()
        with patch.object(
            client.session, "post", side_effect=requests.ConnectionError("down")
        ):
            with self.assertRaises(RucLookupError):
                client.fetch(RUC_ACTIVE)
