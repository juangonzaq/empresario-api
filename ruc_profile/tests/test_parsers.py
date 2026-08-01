"""Tests for parsing the section pages."""

from __future__ import annotations

from django.test import SimpleTestCase

from ruc_profile.services.constants import SECTIONS_BY_KEY
from ruc_profile.services.parsers import (
    parse_legal_representatives,
    parse_section,
    parse_worker_rows,
)

from . import factories as f


def parse(key: str, page: str):
    return parse_section(SECTIONS_BY_KEY[key], page)


class TableSectionTests(SimpleTestCase):
    def test_reads_headers_and_rows(self):
        data = parse("workers", f.workers_page())
        self.assertTrue(data.has_data)
        self.assertEqual(len(data.tables), 1)
        self.assertEqual(data.tables[0].headers[0], "Período")
        self.assertEqual(len(data.tables[0].rows), 3)

    def test_placeholder_row_counts_as_no_data(self):
        data = parse("probatory_acts", f.empty_table_page())
        self.assertFalse(data.has_data)
        self.assertEqual(data.tables, [])

    def test_prose_only_page_counts_as_no_data(self):
        data = parse("coactive_debt", f.no_data_text_page())
        self.assertFalse(data.has_data)

    def test_real_debt_is_detected(self):
        data = parse("coactive_debt", f.coactive_debt_page())
        self.assertTrue(data.has_data)
        self.assertEqual(data.tables[0].rows[0][1], "12500.00")

    def test_a_mixed_section_keeps_the_table_that_has_data(self):
        """Información histórica mixes real tables with 'No hay Información' ones."""
        data = parse("historical", f.historical_page())
        self.assertTrue(data.has_data)
        self.assertEqual(len(data.tables), 1)
        self.assertEqual(data.tables[0].rows[0][0], "HABIDO")

    def test_title_is_captured(self):
        data = parse("workers", f.workers_page())
        self.assertIn("CANTIDAD DE TRABAJADORES", data.title)


class BooleanSectionTests(SimpleTestCase):
    def test_no_answer_means_no_debt(self):
        data = parse("reactiva_peru", f.boolean_page("NO"))
        self.assertIs(data.answer, False)
        self.assertFalse(data.has_data)

    def test_yes_answer_means_debt(self):
        data = parse("reactiva_peru", f.boolean_page("SI"))
        self.assertIs(data.answer, True)
        self.assertTrue(data.has_data)

    def test_accented_yes_is_understood(self):
        self.assertIs(parse("reactiva_peru", f.boolean_page("SÍ")).answer, True)

    def test_missing_answer_is_unknown_not_false(self):
        page = f.PAGE.format(title="REACTIVA", body="<div>sin respuesta</div>")
        self.assertIsNone(parse("reactiva_peru", page).answer)

    def test_double_encoded_entities_are_decoded(self):
        data = parse("reactiva_peru", f.boolean_page("NO"))
        self.assertIn("¿Tiene deuda", data.text)


class RowExtractionTests(SimpleTestCase):
    def test_worker_rows_are_typed(self):
        rows = parse_worker_rows(parse("workers", f.workers_page()))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], {
            "period": "2025-06", "workers": 7, "pensioners": 0, "service_providers": 2,
        })

    def test_a_declared_zero_is_kept(self):
        rows = parse_worker_rows(
            parse("workers", f.workers_page([["2026-05", "0", "0", "0"]]))
        )
        self.assertEqual(rows[0]["workers"], 0)

    def test_non_period_rows_are_ignored(self):
        rows = parse_worker_rows(
            parse("workers", f.workers_page([["total", "7", "0", "2"]]))
        )
        self.assertEqual(rows, [])

    def test_legal_representatives_are_extracted(self):
        rows = parse_legal_representatives(
            parse("legal_representatives", f.representatives_page())
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["document_number"], "70030212")
        self.assertEqual(rows[0]["role"], "GERENTE GENERAL")
