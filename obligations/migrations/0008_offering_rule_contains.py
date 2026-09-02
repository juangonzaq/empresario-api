"""«Qué vendes» pasó a ser lista (igual que los rubros en la 0007): la regla
sanitaria de alimentos deja de mirar el valor único `company.offering` y pasa a
`company.offerings contains food`, para que vender comida junto con otra cosa
siga activando la obligación."""

from django.db import migrations

# code → (aplicabilidad nueva, aplicabilidad anterior)
CHANGES = {
    "muni-food-sanitary": (
        {"any": [
            {"field": "company.sectors", "operator": "contains", "value": "food"},
            {"field": "company.offerings", "operator": "contains", "value": "food"},
        ]},
        {"any": [
            {"field": "company.sectors", "operator": "contains", "value": "food"},
            {"field": "company.offering", "operator": "eq", "value": "food"},
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
    dependencies = [("obligations", "0007_sector_rules_contains")]
    operations = [migrations.RunPython(forwards, backwards)]
