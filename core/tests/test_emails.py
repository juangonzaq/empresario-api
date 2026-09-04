"""Las plantillas de correo se renderizan limpias.

Un comentario ``{# … #}`` partido en dos líneas no es comentario para
Django: se imprime tal cual, y así llegó a la bandeja de un usuario dentro
del código de descarga. Aquí se renderiza cada plantilla con un contexto
genérico y se comprueba que no quede sintaxis de plantilla a la vista.
"""

from __future__ import annotations

from pathlib import Path

from django.template.loader import render_to_string
from django.test import SimpleTestCase

EMAIL_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"


class EmailTemplateTests(SimpleTestCase):
    def test_every_template_renders_without_template_syntax_leaking(self):
        templates = sorted(
            p.name for p in EMAIL_DIR.glob("*.html") if not p.name.startswith("_")
        )
        self.assertTrue(templates)
        context = {
            "subject": "Prueba", "brand": "EMPRESARIO", "frontend_url": "https://empresario.pe",
            "codigo": "123456", "minutos": 10, "etiqueta": "facturas", "empresa": "EMPRESA",
            "cantidad": 3, "nombre": "Ana", "url": "https://empresario.pe/x", "dias": 7,
            "plan": "Mensual", "monto": "S/ 99", "pares": [], "items": [],
        }
        for name in templates:
            with self.subTest(template=name):
                html = render_to_string(f"email/{name}", context)
                for marker in ("{#", "#}", "{%", "{{", "}}"):
                    self.assertNotIn(marker, html, f"{name} deja «{marker}» a la vista")
