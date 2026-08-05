"""Parser tests driven by a real Consulta de ITF response."""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase

from sunat_itf.models import ItfSection
from sunat_itf.services.parsing import (
    current_period,
    iter_records,
    previous_period,
    ytd_range,
)

SAMPLE = (Path(__file__).parent / "sample_itf_result.html").read_text(encoding="utf-8")
TAXPAYER_ID = "20604442533"


class ItfParsingTests(SimpleTestCase):
    def setUp(self):
        self.records = list(iter_records(SAMPLE, TAXPAYER_ID))

    def test_extracts_accumulated_rows(self):
        self.assertTrue(self.records)
        accumulated = [
            r for r in self.records if r["section"] == ItfSection.ACCUMULATED
        ]
        self.assertGreater(len(accumulated), 0)
        first = accumulated[0]
        self.assertEqual(first["taxpayer_id"], TAXPAYER_ID)
        self.assertEqual(first["declarant_ruc"], "20100047218")
        self.assertEqual(first["declarant_name"], "BANCO DE CREDITO DEL PERU")
        self.assertEqual(first["period"], "202601")
        self.assertEqual(first["kind"], "Afecta")
        self.assertEqual(first["movement"], "Usando cuenta")
        self.assertEqual(first["base_amount"], Decimal("26000.00"))
        self.assertEqual(first["tax"], Decimal("1.30"))

    def test_no_header_rows_leak_in(self):
        for record in self.records:
            self.assertNotEqual(record["declarant_ruc"].lower(), "ruc")
            self.assertTrue(record["period"].isdigit())

    def test_contribuyente_column_kept_in_extra(self):
        record = self.records[0]
        self.assertEqual(record["extra"].get("Contribuyente"), TAXPAYER_ID)

    def test_raw_row_is_preserved(self):
        self.assertIsInstance(self.records[0]["raw"], list)
        self.assertIn("BANCO DE CREDITO DEL PERU", self.records[0]["raw"])


class PeriodHelperTests(SimpleTestCase):
    def test_ytd_range_starts_in_january(self):
        self.assertEqual(ytd_range("202608"), ("202601", "202608"))

    def test_current_period(self):
        self.assertEqual(current_period(datetime.date(2026, 8, 4)), "202608")

    def test_previous_period_rolls_year_back_in_january(self):
        self.assertEqual(previous_period(datetime.date(2026, 1, 15)), "202512")
        self.assertEqual(previous_period(datetime.date(2026, 9, 1)), "202608")
