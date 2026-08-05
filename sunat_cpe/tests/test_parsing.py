"""Parser tests over real Consultar Factura responses for several tipos."""

from __future__ import annotations

import datetime
import json
from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase

from sunat_cpe.models import Direction, DocumentClass
from sunat_cpe.services.parsing import (
    current_period,
    month_bounds,
    parse_amount,
    parse_records,
    previous_period,
    recent_periods,
    record_fields,
)

TESTS_DIR = Path(__file__).parent
SAMPLE = (TESTS_DIR / "sample_cpe_response.html").read_text(encoding="utf-8")
ALL_TYPES = json.loads((TESTS_DIR / "sample_cpe_all_types.json").read_text())
ACCOUNT = "20604442533"


class ParseRecordsTests(SimpleTestCase):
    def test_extracts_records(self):
        records = parse_records(SAMPLE)
        self.assertGreater(len(records), 0)
        self.assertEqual(records[0]["nroFacturaDesc"], "E001 - 478")

    def test_safe_on_garbage(self):
        self.assertEqual(parse_records("<html></html>"), [])
        self.assertEqual(parse_records("<textarea>x</textarea>"), [])


class RecordFieldsTests(SimpleTestCase):
    def test_issued_invoice(self):
        f = record_fields(parse_records(SAMPLE)[0], ACCOUNT, "10")
        self.assertEqual(f["direction"], Direction.ISSUED)
        self.assertEqual(f["document_class"], DocumentClass.INVOICE)
        self.assertEqual(f["issuer_ruc"], ACCOUNT)
        self.assertEqual(f["series"], "E001")
        self.assertEqual(f["number"], "478")
        self.assertEqual(f["total_amount"], Decimal("7227.50"))
        self.assertEqual(f["download_code"], "10")

    def test_received_invoice_has_third_party_issuer(self):
        rec = ALL_TYPES["11"][0]
        f = record_fields(rec, ACCOUNT, "11")
        self.assertEqual(f["direction"], Direction.RECEIVED)
        self.assertNotEqual(f["issuer_ruc"], ACCOUNT)
        self.assertEqual(f["receiver_ruc"], ACCOUNT)
        self.assertEqual(f["document_class"], DocumentClass.INVOICE)

    def test_credit_note_class_and_download_code(self):
        rec = ALL_TYPES["13"][0]
        f = record_fields(rec, ACCOUNT, "13")
        self.assertEqual(f["document_class"], DocumentClass.CREDIT_NOTE)
        self.assertEqual(f["cpe_code"], "07")
        self.assertEqual(f["document_type"], "21")   # tipoCPE for NC
        self.assertEqual(f["download_code"], "13")   # codFactura for the XML form
        self.assertEqual(f["direction"], Direction.ISSUED)


class HelperTests(SimpleTestCase):
    def test_amount_and_dates(self):
        self.assertEqual(parse_amount("S/7,227.50"), Decimal("7227.50"))
        self.assertEqual(month_bounds("202607"), ("01/07/2026", "31/07/2026"))

    def test_period_walkers(self):
        self.assertEqual(previous_period("202601"), "202512")
        self.assertEqual(current_period(datetime.date(2026, 7, 9)), "202607")
        self.assertEqual(
            recent_periods(2, datetime.date(2026, 7, 9)), ["202607", "202606", "202605"]
        )
