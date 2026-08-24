"""Add a food-sector obligation that only applies when the company's business
profile says it sells food — a visible example of the profile driving the map."""

from django.db import migrations

RULE = {
    "code": "muni-food-sanitary",
    "title": "Sanidad para alimentos (carné y registro sanitario)",
    "summary": "Si vendes alimentos o bebidas, necesitas carné de sanidad del personal y, según el caso, registro sanitario.",
    "obligation_type": "legal",
    "default_severity": "medium",
    "frequency": "continuous",
    "applicability": {"any": [
        {"field": "company.sector", "operator": "eq", "value": "food"},
        {"field": "company.offering", "operator": "eq", "value": "food"},
    ]},
    "evaluator_key": "",
    "legal_reference": "Ley de Inocuidad de los Alimentos · DIGESA / Municipalidad",
    "source_name": "DIGESA",
    "source_url": "https://www.gob.pe/digesa",
    "remediation_steps": [
        "Tramita el carné de sanidad de quienes manipulan alimentos.",
        "Verifica si tu producto requiere registro sanitario y gestiónalo.",
    ],
    "sort_order": 2,
}


def seed(apps, schema_editor):
    Domain = apps.get_model("obligations", "ComplianceDomain")
    Rule = apps.get_model("obligations", "ComplianceRule")
    municipal = Domain.objects.filter(code="MUNICIPAL").first()
    if municipal is None:
        return
    Rule.objects.get_or_create(code=RULE["code"], defaults={**RULE, "domain": municipal})


def unseed(apps, schema_editor):
    Rule = apps.get_model("obligations", "ComplianceRule")
    Rule.objects.filter(code=RULE["code"]).delete()


class Migration(migrations.Migration):
    dependencies = [("obligations", "0003_alter_obligationassessment_input_snapshot")]
    operations = [migrations.RunPython(seed, unseed)]
