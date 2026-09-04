"""La ruta de la carpeta de comprobantes es un contrato: cambiarla mueve los
archivos de todas las empresas."""

from __future__ import annotations

from django.test import SimpleTestCase

from core.archive import document_path


class DocumentPathTests(SimpleTestCase):
    def test_layout_is_ruc_year_month_kind_code_uuid(self):
        path = document_path(
            account_ruc="20604442533", period="202609", kind="factura",
            code="F001-123", pk="0192abcd", extension=".XML",
        )
        self.assertEqual(
            path, "comprobantes/20604442533/2026/09/factura/F001-123-0192abcd.xml"
        )

    def test_missing_period_lands_in_zero_folders(self):
        path = document_path(
            account_ruc="20604442533", period="", kind="nota_credito",
            code="FC01-7", pk="x", extension="xml",
        )
        self.assertTrue(path.startswith("comprobantes/20604442533/0000/00/nota_credito/"))

    def test_code_is_cleaned_for_the_filesystem(self):
        path = document_path(
            account_ruc="20604442533", period="202607", kind="factura",
            code="E001 - 478", pk="x", extension="xml",
        )
        self.assertTrue(path.endswith("/E001-478-x.xml"))
        empty = document_path(
            account_ruc="20604442533", period="202607", kind="factura",
            code="", pk="x", extension="xml",
        )
        self.assertTrue(empty.endswith("/sin-numero-x.xml"))
