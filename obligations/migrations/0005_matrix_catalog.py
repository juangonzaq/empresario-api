"""Grow the catalog from the responsibility matrix research (matriz de
responsabilidades empresariales Perú) — the transversal rules an SME can
actually be told about with the facts the platform holds.

Two moves, both idempotent:

* **Sharpen applicability** of existing rules so they key off real facts
  instead of applying to everyone: municipal rules require a physical
  premises, corporate rules a juridical person (RUC 20…), labor rules a
  headcount. With ternary applicability, a missing fact now shows as
  «por determinar» + the pending question instead of a silent false match.
* **Add rules** the matrix marks as high-value for SMEs and that our context
  can condition: labor thresholds (gratificaciones, Vida Ley, hostigamiento,
  RIT >100, cuota de discapacidad >50), sector contributions (SENCICO,
  SENATI), bancarización, and a new Consumidor domain with the Libro de
  Reclamaciones in its physical and virtual forms.
"""

from django.db import migrations

NEW_DOMAINS = [
    ("CONSUMER", "Consumidor", "Protección al consumidor y canales de venta.", 5),
]

# code -> nueva expresión de aplicabilidad (reemplaza la sembrada).
APPLICABILITY_UPDATES = {
    # Municipal: solo con local físico; un negocio 100% digital no la necesita.
    "muni-license": {"all": [{"field": "company.has_premises", "operator": "truthy"}]},
    "muni-itse": {"all": [{"field": "company.has_premises", "operator": "truthy"}]},
    # Societario: solo personas jurídicas (RUC 20…); una persona natural con
    # negocio no celebra juntas ni lleva libros societarios.
    "corp-annual-shareholders": {"all": [{"field": "company.is_juridical", "operator": "truthy"}]},
    "corp-legal-books": {"all": [{"field": "company.is_juridical", "operator": "truthy"}]},
    # Laboral: la señal combinada de headcount (planilla, ficha RUC o perfil),
    # no solo los colaboradores registrados en la plataforma.
    "labor-sst-committee": {"all": [{"field": "company.has_payroll", "operator": "truthy"}]},
    # Datos: también si maneja datos de clientes, no solo de planilla.
    "data-registry": {"any": [
        {"field": "company.has_payroll", "operator": "truthy"},
        {"field": "company.sells_to_consumers", "operator": "truthy"},
    ]},
}

# domain, code, title, summary, type, severity, frequency, applicability,
# evaluator_key, legal_reference, source_name, source_url, remediation, sort
NEW_RULES = [
    # ── Laboral: umbrales de la matriz ──
    ("LABOR", "labor-gratificaciones", "Gratificaciones de julio y diciembre",
     "Paga la gratificación legal (y su bonificación extraordinaria) hasta el 15 de julio y el 15 de diciembre.",
     "legal", "high", "event_driven",
     {"all": [{"field": "company.has_payroll", "operator": "truthy"}]},
     "", "Ley 27735 · régimen laboral aplicable", "SUNAFIL", "https://www.gob.pe/sunafil",
     ["Calcula la gratificación según el régimen de cada trabajador (general o MYPE).",
      "Deposítala con su bonificación antes del 15 de julio y del 15 de diciembre."], 4),

    ("LABOR", "labor-vida-ley", "Seguro Vida Ley desde el primer día",
     "Todo trabajador debe estar cubierto por el seguro Vida Ley desde el inicio de la relación laboral.",
     "legal", "medium", "continuous",
     {"all": [{"field": "company.has_payroll", "operator": "truthy"}]},
     "", "D. Leg. 688 · Ley de Consolidación de Beneficios Sociales", "SBS",
     "https://www.sbs.gob.pe/usuarios/aprende-con-la-sbs/seguro-vida-ley",
     ["Contrata la póliza Vida Ley e incluye a todo tu personal desde su primer día.",
      "Mantén la nómina asegurada al día y registra el contrato en el MTPE."], 5),

    ("LABOR", "labor-hostigamiento", "Prevención del hostigamiento sexual laboral",
     "Con 20 o más trabajadores necesitas un comité; con menos, un delegado. Siempre: política, canal de denuncia y capacitación.",
     "legal", "medium", "continuous",
     {"all": [{"field": "company.has_payroll", "operator": "truthy"}]},
     "", "Ley 27942 y su reglamento", "SUNAFIL", "https://www.gob.pe/sunafil",
     ["Designa el comité (20 o más trabajadores) o el delegado contra el hostigamiento.",
      "Aprueba la política, difunde el canal de denuncias y capacita a tu equipo."], 6),

    ("LABOR", "labor-rit", "Reglamento Interno de Trabajo (más de 100 trabajadores)",
     "Al superar los 100 trabajadores debes aprobar y presentar el Reglamento Interno de Trabajo ante el MTPE.",
     "legal", "low", "event_driven",
     {"all": [{"field": "company.worker_count", "operator": "gt", "value": 100}]},
     "", "D.S. 039-91-TR", "MTPE", "https://www.gob.pe/mtpe",
     ["Redacta el RIT y preséntalo ante el MTPE.",
      "Entrega una copia a cada trabajador y aplícalo."], 7),

    ("LABOR", "labor-cuota-discapacidad", "Cuota de empleo de personas con discapacidad",
     "Con más de 50 trabajadores (promedio anual), al menos el 3% de tu personal deben ser personas con discapacidad.",
     "legal", "medium", "annual",
     {"all": [{"field": "company.worker_count", "operator": "gt", "value": 50}]},
     "", "Ley 29973 · art. 49", "MTPE / CONADIS", "https://www.gob.pe/mtpe",
     ["Calcula tu promedio anual de trabajadores.",
      "Verifica que el 3% o más sean personas con discapacidad, o sustenta la excepción."], 8),

    # ── Tributario: contribuciones por sector y reglas transversales ──
    ("TAX", "tax-sencico", "Contribución al SENCICO",
     "Si desarrollas actividades de construcción, aportas el 0.2% sobre lo facturado por esas actividades.",
     "legal", "medium", "monthly",
     {"all": [{"field": "company.sector", "operator": "eq", "value": "construction"}]},
     "", "D. Leg. 147 · Ley del SENCICO", "SENCICO",
     "https://www.gob.pe/institucion/sencico/campa%C3%B1as/2860-contribucion-al-sencico",
     ["Declara y paga la contribución junto con tus tributos mensuales.",
      "Presenta la declaración jurada anual del SENCICO."], 5),

    ("TAX", "tax-senati", "Contribución al SENATI",
     "Empresas industriales con más de 20 trabajadores aportan al SENATI sobre las remuneraciones de su personal.",
     "legal", "medium", "monthly",
     {"all": [
         {"field": "company.sector", "operator": "eq", "value": "manufacturing"},
         {"field": "company.worker_count", "operator": "gt", "value": 20},
     ]},
     "", "Ley 26272 · Ley del SENATI", "SENATI", "https://www.senati.edu.pe/content/contribuciones",
     ["Inscríbete como contribuyente del SENATI.",
      "Declara y paga mensualmente; presenta la DJ anual."], 6),

    ("TAX", "tax-bancarizacion", "Bancarización de pagos desde S/ 2,000",
     "Todo pago desde S/ 2,000 (o US$ 500) debe hacerse por un medio de pago financiero; si no, pierdes el gasto y el crédito fiscal.",
     "legal", "medium", "continuous", {},
     "", "Ley 28194 · Ley de Bancarización e ITF", "SUNAT",
     "https://emprender.sunat.gob.pe/comprobantes-libros/comprobantes-pago/bancarizacion",
     ["Paga por banco (transferencia, depósito, cheque no negociable) desde el umbral.",
      "Guarda el voucher junto al comprobante: es tu sustento de gasto y crédito."], 7),

    # ── Consumidor ──
    ("CONSUMER", "consumer-libro-reclamaciones", "Libro de Reclamaciones en tu local",
     "Si vendes a consumidores finales en un local, necesitas Libro de Reclamaciones (físico o virtual) y su aviso visible.",
     "legal", "medium", "continuous",
     {"all": [
         {"field": "company.sells_to_consumers", "operator": "truthy"},
         {"field": "company.has_premises", "operator": "truthy"},
     ]},
     "", "Código de Protección al Consumidor (Ley 29571) · D.S. 011-2011-PCM",
     "Indecopi", "https://consumidor.gob.pe/libro-de-reclamaciones/",
     ["Implementa el Libro de Reclamaciones y coloca el aviso en un lugar visible.",
      "Responde cada reclamo en máximo 15 días hábiles, sin prórroga."], 0),

    ("CONSUMER", "consumer-libro-virtual", "Libro de Reclamaciones en tu canal digital",
     "Si vendes por internet a consumidores finales, tu web o app debe tener un Libro de Reclamaciones virtual accesible.",
     "legal", "medium", "continuous",
     {"all": [
         {"field": "company.sells_to_consumers", "operator": "truthy"},
         {"field": "company.sells_online", "operator": "truthy"},
     ]},
     "", "Código de Protección al Consumidor (Ley 29571) · D.S. 011-2011-PCM",
     "Indecopi", "https://consumidor.gob.pe/libro-de-reclamaciones/",
     ["Agrega el Libro de Reclamaciones virtual a tu web o app, visible desde la portada.",
      "Responde cada reclamo en máximo 15 días hábiles y guarda la constancia."], 1),
]


def seed(apps, schema_editor):
    Domain = apps.get_model("obligations", "ComplianceDomain")
    Rule = apps.get_model("obligations", "ComplianceRule")

    domains = {d.code: d for d in Domain.objects.all()}
    for code, name, description, order in NEW_DOMAINS:
        domains[code], _ = Domain.objects.get_or_create(
            code=code, defaults={"name": name, "description": description, "sort_order": order},
        )

    for code, applicability in APPLICABILITY_UPDATES.items():
        Rule.objects.filter(code=code).update(applicability=applicability)

    for (dcode, code, title, summary, otype, severity, freq, applicability,
         evaluator, legal, source_name, source_url, remediation, order) in NEW_RULES:
        Rule.objects.get_or_create(
            code=code,
            defaults={
                "domain": domains[dcode],
                "title": title,
                "summary": summary,
                "obligation_type": otype,
                "default_severity": severity,
                "frequency": freq,
                "applicability": applicability,
                "evaluator_key": evaluator,
                "legal_reference": legal,
                "source_name": source_name,
                "source_url": source_url,
                "remediation_steps": remediation,
                "sort_order": order,
            },
        )


def unseed(apps, schema_editor):
    Rule = apps.get_model("obligations", "ComplianceRule")
    Domain = apps.get_model("obligations", "ComplianceDomain")
    Rule.objects.filter(code__in=[r[1] for r in NEW_RULES]).delete()
    Domain.objects.filter(code__in=[d[0] for d in NEW_DOMAINS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("obligations", "0004_food_rule"),
        # El contexto lee los tri-estados nuevos del perfil del negocio.
        ("accounts", "0009_businessprofile_has_premises_and_more"),
    ]
    operations = [migrations.RunPython(seed, unseed)]
