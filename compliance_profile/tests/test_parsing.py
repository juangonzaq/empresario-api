"""Tests for the SUNAT compliance payload parsers."""

from __future__ import annotations

from django.test import SimpleTestCase

from compliance_profile.services.parsing import (
    header_fields,
    iter_detail_variables,
    parse_datetime,
    parse_int,
)

from .samples import CURRENT_HEADER, DETAIL


class ParseHelpersTests(SimpleTestCase):
    def test_parse_int_handles_garbage(self):
        self.assertEqual(parse_int("202602"), 202602)
        self.assertIsNone(parse_int(None))
        self.assertIsNone(parse_int("n/a"))

    def test_parse_datetime_keeps_sunat_offset(self):
        parsed = parse_datetime("2026-07-04T00:00:00.000-05:00")
        self.assertEqual(parsed.isoformat(), "2026-07-04T00:00:00-05:00")
        self.assertIsNone(parse_datetime(""))
        self.assertIsNone(parse_datetime("not-a-date"))


class HeaderFieldsTests(SimpleTestCase):
    def test_maps_every_field(self):
        fields = header_fields(CURRENT_HEADER)
        self.assertEqual(fields["execution_period"], 202605)
        self.assertEqual(fields["preliminary_category"], "D")
        self.assertEqual(fields["rating"], "D")
        self.assertEqual(fields["evaluation_start"], 202507)
        self.assertEqual(fields["evaluation_end"], 202606)
        self.assertEqual(fields["data_location_code"], 2)
        self.assertEqual(fields["loaded_at"].year, 2026)
        self.assertIs(fields["header_payload"], CURRENT_HEADER)

    def test_tolerates_missing_values(self):
        fields = header_fields({"numRuc": "123"})
        self.assertEqual(fields["rating"], "")
        self.assertIsNone(fields["loaded_at"])


class DetailVariablesTests(SimpleTestCase):
    def test_yields_one_row_per_variable(self):
        rows = list(iter_detail_variables(DETAIL))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["variable_type"], "P")
        self.assertEqual(row["type_label"], "Ponderación")
        self.assertEqual(row["code"], "v0615")
        self.assertEqual(row["severity"], "Muy grave")
        self.assertEqual(row["record_count"], 2)
        self.assertEqual(row["observation"]["desEstado"], "Pendiente")

    def test_null_sections_and_empty_detail_are_skipped(self):
        self.assertEqual(list(iter_detail_variables(None)), [])
        self.assertEqual(
            list(iter_detail_variables({"varPonderacion": None})), []
        )
