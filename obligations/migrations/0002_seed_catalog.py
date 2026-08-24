"""Seed the compliance catalog: domains + a starter set of real Peruvian SME
obligations. Idempotent (get_or_create by code), so it is safe to re-run and to
extend in later migrations.

This is a *starting* catalog, not the full universe of obligations. It should be
reviewed by an accountant/lawyer and grown over time from the admin. Deadlines
for the tax/labor calendar keep living in ``sensor_sunat`` (the /calendario
engine); these rules describe the obligation and its status, and link there.
"""

from django.db import migrations

DOMAINS = [
    ("TAX", "Tributario", "Obligaciones ante SUNAT.", 0),
    ("LABOR", "Laboral", "Planilla, seguridad social y fiscalización laboral.", 1),
    ("CORPORATE", "Societario", "Vida societaria y libros de la empresa.", 2),
    ("DATA_PROTECTION", "Datos personales", "Protección de datos personales.", 3),
    ("MUNICIPAL", "Municipal", "Licencias y permisos municipales.", 4),
]

# domain, code, title, summary, type, severity, frequency, applicability,
# evaluator_key, legal_reference, source_name, source_url, remediation, sort
RULES = [
    # ── Tributario ──
    ("TAX", "tax-monthly-igv-renta", "Declaración mensual de IGV-Renta",
     "Cada mes debes declarar tus ventas y compras y pagar el IGV y el pago a cuenta de renta.",
     "legal", "high", "monthly", {}, "tax_monthly_declaration",
     "Formulario Virtual 621 · Código Tributario", "SUNAT",
     "https://www.gob.pe/sunat",
     ["Reúne tus ventas y compras del periodo.",
      "Presenta el Formulario Virtual 621 dentro del cronograma según tu último dígito de RUC.",
      "Paga el IGV y el pago a cuenta de renta."], 0),

    ("TAX", "tax-annual-income", "Declaración Jurada Anual de Renta",
     "Las empresas del Régimen General y MYPE Tributario presentan la DJ Anual de Renta.",
     "legal", "high", "annual",
     {"all": [{"field": "company.tax_regime", "operator": "in", "value": ["RMT", "RG"]}]},
     "", "TUO de la Ley del Impuesto a la Renta", "SUNAT",
     "https://www.gob.pe/sunat",
     ["Cierra tu contabilidad del ejercicio.",
      "Presenta la Declaración Jurada Anual dentro del cronograma anual de SUNAT."], 1),

    ("TAX", "tax-electronic-books-sire", "Libros electrónicos (SIRE: RVIE y RCE)",
     "Llevar el Registro de Ventas e Ingresos y el Registro de Compras de forma electrónica en el SIRE.",
     "legal", "medium", "monthly",
     {"all": [{"field": "company.tax_regime", "operator": "in", "value": ["RER", "RMT", "RG"]}]},
     "", "Resolución de Superintendencia SIRE", "SUNAT",
     "https://www.gob.pe/sunat",
     ["Genera y confirma tu RVIE y RCE cada mes en el SIRE.",
      "Concilia el SIRE con tus comprobantes antes de declarar."], 2),

    ("TAX", "tax-consistency-control", "Consistencia entre lo declarado y tus comprobantes",
     "Control preventivo: que tus declaraciones cuadren con tus comprobantes electrónicos y tu banca.",
     "preventive_control", "medium", "continuous", {}, "consistency_control",
     "Buenas prácticas · fiscalización SUNAT", "Empresario",
     "", ["Corre la conciliación en Finanzas › Conciliación.",
          "Revisa y justifica las diferencias antes de declarar."], 3),

    ("TAX", "tax-risk-signals", "Ficha RUC sin señales de riesgo",
     "Control preventivo: tu Ficha RUC no muestra deuda coactiva, omisiones ni actos probatorios.",
     "preventive_control", "high", "continuous", {}, "risk_signals_clear",
     "Ficha RUC · SUNAT", "SUNAT", "https://www.gob.pe/sunat",
     ["Revisa las señales marcadas en tu Ficha RUC.",
      "Regulariza deudas u omisiones pendientes."], 4),

    # ── Laboral ──
    ("LABOR", "labor-tregistro", "Alta de trabajadores en el T-Registro",
     "Registra a cada trabajador en el T-Registro antes del inicio de labores.",
     "legal", "high", "event_driven",
     {"all": [{"field": "company.has_payroll", "operator": "truthy"}]},
     "payroll_registration", "D.S. 018-2007-TR · SUNAT-T-Registro", "SUNAT",
     "https://www.gob.pe/sunat",
     ["Da de alta a cada trabajador en el T-Registro antes de su primer día.",
      "Mantén actualizados los datos de altas y bajas."], 0),

    ("LABOR", "labor-plame", "PLAME – Planilla Electrónica mensual",
     "Presenta la Planilla Mensual (PLAME) con remuneraciones, aportes y retenciones.",
     "legal", "high", "monthly",
     {"all": [{"field": "company.has_payroll", "operator": "truthy"}]},
     "", "Planilla Electrónica · SUNAT", "SUNAT", "https://www.gob.pe/sunat",
     ["Cierra tu planilla del mes.",
      "Presenta el PLAME y paga EsSalud, ONP/AFP y retenciones dentro del cronograma."], 1),

    ("LABOR", "labor-cts", "Depósito de CTS (mayo y noviembre)",
     "Deposita la Compensación por Tiempo de Servicios dos veces al año.",
     "legal", "medium", "event_driven",
     {"all": [{"field": "company.has_payroll", "operator": "truthy"}]},
     "", "TUO Ley de CTS · D.S. 001-97-TR", "MTPE", "https://www.gob.pe/mtpe",
     ["Calcula la CTS de cada trabajador.",
      "Deposita antes del 15 de mayo y del 15 de noviembre."], 2),

    ("LABOR", "labor-sst-committee", "Seguridad y Salud en el Trabajo (SST)",
     "Reglamento interno de SST y comité o supervisor de seguridad según tu número de trabajadores.",
     "legal", "medium", "continuous",
     {"all": [{"field": "company.active_employee_count", "operator": "gt", "value": 0}]},
     "", "Ley 29783 de Seguridad y Salud en el Trabajo", "MTPE",
     "https://www.gob.pe/mtpe",
     ["Con 20 o más trabajadores: conforma el Comité de SST; con menos, un supervisor.",
      "Aprueba el reglamento interno de SST y la matriz de riesgos."], 3),

    # ── Societario ──
    ("CORPORATE", "corp-annual-shareholders", "Junta Obligatoria Anual de accionistas",
     "Si tu empresa es una sociedad (S.A.C./S.A.), celebra la junta obligatoria anual dentro del primer trimestre.",
     "legal", "low", "annual", {}, "",
     "Ley General de Sociedades (Ley 26887)", "SUNARP", "https://www.gob.pe/sunarp",
     ["Convoca y celebra la junta obligatoria anual.",
      "Aprueba estados financieros y aplicación de utilidades; deja acta."], 0),

    ("CORPORATE", "corp-legal-books", "Legalización de libros societarios y contables",
     "Mantén legalizados tus libros de actas y los libros contables que te corresponden.",
     "legal", "low", "one_time", {}, "",
     "Ley General de Sociedades · Código de Comercio", "Notaría / SUNARP", "",
     ["Legaliza el libro de actas y los libros contables obligatorios.",
      "Legaliza un nuevo libro antes de agotar el anterior."], 1),

    # ── Datos personales ──
    ("DATA_PROTECTION", "data-registry", "Inscripción de bancos de datos personales",
     "Si tratas datos personales (por ejemplo, de tu planilla o tus clientes), inscribe tus bancos de datos.",
     "legal", "medium", "one_time",
     {"any": [{"field": "company.has_payroll", "operator": "truthy"}]},
     "", "Ley 29733 de Protección de Datos Personales", "ANPD (MINJUS)",
     "https://www.gob.pe/minjus",
     ["Identifica tus bancos de datos personales.",
      "Inscríbelos en el Registro Nacional de Protección de Datos Personales."], 0),

    ("DATA_PROTECTION", "data-privacy-policy", "Política de privacidad y consentimiento",
     "Informa y obtén el consentimiento para el tratamiento de datos personales.",
     "recommendation", "low", "continuous", {}, "",
     "Ley 29733 · Reglamento", "ANPD (MINJUS)", "https://www.gob.pe/minjus",
     ["Publica tu política de privacidad.",
      "Obtén y conserva el consentimiento informado cuando corresponda."], 1),

    # ── Municipal ──
    ("MUNICIPAL", "muni-license", "Licencia de funcionamiento vigente",
     "Tu local debe contar con licencia de funcionamiento de la municipalidad.",
     "legal", "medium", "one_time", {}, "",
     "Ley 28976 de Licencia de Funcionamiento", "Municipalidad", "",
     ["Tramita o renueva la licencia de funcionamiento de tu local."], 0),

    ("MUNICIPAL", "muni-itse", "Certificado de Inspección Técnica de Seguridad (ITSE)",
     "Certificado de seguridad en edificaciones (Defensa Civil), requisito de la licencia.",
     "legal", "medium", "event_driven", {}, "",
     "Reglamento de Inspecciones Técnicas de Seguridad en Edificaciones", "Municipalidad / CENEPRED", "",
     ["Solicita o renueva el certificado ITSE de tu local."], 1),
]


def seed(apps, schema_editor):
    Domain = apps.get_model("obligations", "ComplianceDomain")
    Rule = apps.get_model("obligations", "ComplianceRule")

    domains = {}
    for code, name, description, order in DOMAINS:
        domains[code], _ = Domain.objects.get_or_create(
            code=code, defaults={"name": name, "description": description, "sort_order": order},
        )

    for (dcode, code, title, summary, otype, severity, freq, applicability,
         evaluator, legal, source_name, source_url, remediation, order) in RULES:
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
    Rule.objects.filter(code__in=[r[1] for r in RULES]).delete()
    Domain.objects.filter(code__in=[d[0] for d in DOMAINS]).delete()


class Migration(migrations.Migration):
    dependencies = [("obligations", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
