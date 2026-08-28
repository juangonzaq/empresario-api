"""Dos obligaciones que ahora se pueden evaluar solas con la consulta de
declaraciones y pagos de SUNAT: la PLAME mensual y el pago de tributos."""

from django.db import migrations

RULES = [
    ("TAX", "tax-monthly-plame", "Declaración mensual de planilla (PLAME)",
     "Si tienes trabajadores, cada mes debes presentar la PLAME y pagar EsSalud, ONP y las retenciones.",
     "legal", "high", "monthly",
     {"all": [{"field": "company.has_payroll", "operator": "truthy"}]},
     "plame_declaration", "Formulario Virtual 0601 · D.S. 018-2007-TR", "SUNAT",
     "https://www.gob.pe/sunat",
     ["Cierra la planilla del mes.", "Presenta la PLAME dentro del cronograma según tu último dígito de RUC.",
      "Paga EsSalud, ONP y las retenciones de 4.ª y 5.ª."], 2),
    ("TAX", "tax-payments-current", "Tributos declarados al día",
     "Lo que declaras debe estar pagado: sin omisiones, sin deuda pendiente del 621 y sin multas recientes.",
     "preventive_control", "high", "continuous", {},
     "tax_payments_current", "Código Tributario · arts. 28-33", "SUNAT",
     "https://www.gob.pe/sunat",
     ["Revisa las alertas de declaraciones en Finanzas.", "Paga o fracciona la deuda pendiente antes de que corran más intereses."], 3),
]


def seed(apps, schema_editor):
    Domain = apps.get_model("obligations", "ComplianceDomain")
    Rule = apps.get_model("obligations", "ComplianceRule")
    for (dcode, code, title, summary, otype, severity, freq, applicability,
         evaluator, legal, source_name, source_url, remediation, order) in RULES:
        domain = Domain.objects.filter(code=dcode).first()
        if domain is None:  # pragma: no cover
            continue
        Rule.objects.get_or_create(code=code, defaults={
            "domain": domain, "title": title, "summary": summary, "obligation_type": otype,
            "default_severity": severity, "frequency": freq, "applicability": applicability,
            "evaluator_key": evaluator, "legal_reference": legal, "source_name": source_name,
            "source_url": source_url, "remediation_steps": remediation, "sort_order": order,
        })


def unseed(apps, schema_editor):
    apps.get_model("obligations", "ComplianceRule").objects.filter(code__in=[r[1] for r in RULES]).delete()


class Migration(migrations.Migration):
    dependencies = [("obligations", "0005_matrix_catalog")]
    operations = [migrations.RunPython(seed, unseed)]
