"""El lector de EECC no muere con un PDF de estructura rota.

Caso real (2026-09-02): un estado de cuenta bancario que cualquier visor
abría hacía morir a pypdf con «Cannot find Root object in pdf». El extractor
cae a pdfminer, que reconstruye el índice escaneando los objetos.
"""

from __future__ import annotations

import io
from unittest.mock import patch

from django.test import SimpleTestCase

from reconciliation.engine import statements


def _pdf_de_prueba() -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, "01/07 ABONO TRANSFERENCIA RECIBIDA 1,500.00")
    c.save()
    return buf.getvalue()


class FallbackLectorTests(SimpleTestCase):
    def test_pdf_que_rompe_pypdf_cae_a_pdfminer(self):
        pdf = _pdf_de_prueba()
        with patch.object(
            statements, "_extract_pypdf",
            side_effect=Exception("Cannot find Root object in pdf"),
        ):
            texto = statements.extract_text(pdf, [])
        self.assertIn("ABONO", texto)

    def test_contrasena_mala_no_se_disfraza_de_pdf_roto(self):
        # WrongPassword debe llegar tal cual al usuario («la clave no abrió el
        # PDF»), no convertirse en un reintento con otro lector.
        with patch.object(
            statements, "_extract_pypdf",
            side_effect=statements.WrongPassword("ninguna abrió"),
        ):
            with self.assertRaises(statements.WrongPassword):
                statements.extract_text(b"%PDF-1.4", ["1234"])


class EnvoltorioBancarioTests(SimpleTestCase):
    """El EECC del BCP llega envuelto en $BOP$…$EOP$: el PDF real va adentro."""

    def test_recorta_el_envoltorio_y_lee(self):
        pdf = _pdf_de_prueba()
        envuelto = b"$BOP$" + pdf + b"$EOP$$BOP$$EOP$"
        texto = statements.extract_text(envuelto, [])
        self.assertIn("ABONO", texto)

    def test_un_pdf_sano_pasa_intacto(self):
        pdf = _pdf_de_prueba()
        self.assertEqual(statements._sanitize_pdf(pdf), pdf)

    def test_algo_que_no_es_pdf_no_se_recorta(self):
        basura = b"<html>no soy un pdf</html>"
        self.assertEqual(statements._sanitize_pdf(basura), basura)
