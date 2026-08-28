"""Las reglas que dependían del rubro único (`company.sector eq X`) pasan a
mirar la lista de rubros (`company.sectors contains X`): un negocio que es
restaurante y además hace obra tiene las dos obligaciones, no una."""

from django.db import migrations

# code → (aplicabilidad nueva, aplicabilidad anterior)
CHANGES = {
    "muni-food-sanitary": (
        {"any": [
            {"field": "company.sectors", "operator": "contains", "value": "food"},
            {"field": "company.offering", "operator": "eq", "value": "food"},
        ]},
        {"any": [
            {"field": "company.sector", "operator": "eq", "value": "food"},
            {"field": "company.offering", "operator": "eq", "value": "food"},
        ]},
    ),
    "tax-sencico": (
        {"all": [{"field": "company.sectors", "operator": "contains", "value": "construction"}]},
        {"all": [{"field": "company.sector", "operator": "eq", "value": "construction"}]},
    ),
    "tax-senati": (
        {"all": [
            {"field": "company.sectors", "operator": "contains", "value": "manufacturing"},
            {"field": "company.worker_count", "operator": "gt", "value": 20},
        ]},
        {"all": [
            {"field": "company.sector", "operator": "eq", "value": "manufacturing"},
            {"field": "company.worker_count", "operator": "gt", "value": 20},
        ]},
    ),
}


def _apply(apps, index):
    Rule = apps.get_model("obligations", "ComplianceRule")
    for code, versions in CHANGES.items():
        Rule.objects.filter(code=code).update(applicability=versions[index])


def forwards(apps, schema_editor):
    _apply(apps, 0)


def backwards(apps, schema_editor):
    _apply(apps, 1)


class Migration(migrations.Migration):
    dependencies = [("obligations", "0006_declaraciones_rules")]
    operations = [migrations.RunPython(forwards, backwards)]
