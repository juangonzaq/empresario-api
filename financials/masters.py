"""Master-data management API — the heart of the platform, editable from
the app itself, not only the Django admin.

One registry describes every master (payroll and financials): its model,
its editable fields and its scope. A single generic view serves list,
create, update and delete for all of them, so adding a master is one
registry entry, not a new endpoint.

Scopes:
* ``global`` — national parameters (UIT, RMV, tax rates, brackets…). They
  affect every company, so editing them is telling the platform what the
  law says; the screen labels them as such.
* ``company`` — the company's own policy (payroll settings, concepts,
  categories, rules). Rows are isolated by ``taxpayer_id``.
"""

from __future__ import annotations

from django.apps import apps as django_apps
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.tenancy import ManagedOrganizationAPIView

# key → (app_label, model, scope, fields, group label, description)
REGISTRY: dict[str, dict] = {
    # ── Planilla ──
    "tax-unit": {
        "model": ("payroll", "TaxUnitValue"), "scope": "global",
        "group": "Planilla", "name": "UIT por ejercicio",
        "fields": ["year", "amount"],
    },
    "minimum-wage": {
        "model": ("payroll", "MinimumWage"), "scope": "global",
        "group": "Planilla", "name": "Remuneración mínima",
        "fields": ["amount", "effective_from", "effective_to"],
    },
    "pension-rates": {
        "model": ("payroll", "PensionFundRate"), "scope": "global",
        "group": "Planilla", "name": "Tasas AFP / ONP",
        "fields": [
            "pension_fund", "commission_type", "mandatory_contribution_rate",
            "flow_commission_rate", "insurance_premium_rate",
            "insurable_ceiling", "effective_from", "effective_to",
        ],
    },
    "tax-brackets": {
        "model": ("payroll", "IncomeTaxBracket"), "scope": "global",
        "group": "Planilla", "name": "Tramos de renta de 5.ª",
        "fields": ["year", "order", "width_in_tax_units", "rate"],
    },
    "income-tax-settings": {
        "model": ("payroll", "IncomeTaxSettings"), "scope": "global",
        "group": "Planilla", "name": "Parámetros de renta",
        "fields": [
            "year", "standard_deduction_in_tax_units", "months_in_projection",
            "statutory_bonus_count",
        ],
    },
    "health-rates": {
        "model": ("payroll", "SocialHealthRate"), "scope": "global",
        "group": "Planilla", "name": "Tasas de salud (EsSalud / EPS)",
        "fields": [
            "standard_rate", "eps_rate", "applies_minimum_wage_floor",
            "extraordinary_bonus_rate", "effective_from", "effective_to",
        ],
    },
    "sctr-rates": {
        "model": ("payroll", "WorkRiskInsuranceRate"), "scope": "global",
        "group": "Planilla", "name": "Tasas SCTR",
        "fields": [
            "activity_code", "health_rate", "pension_rate",
            "effective_from", "effective_to",
        ],
    },
    "payroll-settings": {
        "model": ("payroll", "PayrollSettings"), "scope": "company",
        "group": "Planilla", "name": "Política de planilla",
        "fields": [
            "days_per_month", "hours_per_day", "first_overtime_hours",
            "first_overtime_surcharge", "additional_overtime_surcharge",
            "family_allowance_rate", "net_variation_alert_pct",
            "payslip_footer_text",
        ],
        "singleton": True,
    },
    "payroll-concepts": {
        "model": ("payroll", "PayrollConcept"), "scope": "company_or_global",
        "group": "Planilla", "name": "Conceptos de planilla",
        "fields": [
            "code", "name", "kind", "affects_pension_base",
            "affects_income_tax_base", "is_computed", "display_order",
            "is_active",
        ],
    },
    # ── Finanzas ──
    "tax-rates": {
        "model": ("financials", "TaxRate"), "scope": "global",
        "group": "Finanzas", "name": "Tasas de impuestos (IGV, ITF)",
        "fields": ["tax_code", "rate", "effective_from", "effective_to"],
    },
    "exchange-rates": {
        "model": ("financials", "ExchangeRate"), "scope": "global",
        "group": "Finanzas", "name": "Tipos de cambio",
        "fields": ["currency", "rate_date", "buy_rate", "sell_rate", "source"],
    },
    "categories": {
        "model": ("financials", "TransactionCategory"), "scope": "company_or_global",
        "group": "Finanzas", "name": "Categorías de transacción",
        "fields": [
            "code", "name", "statement", "sign", "applies_to",
            "display_order", "is_active",
        ],
    },
    "statement-lines": {
        "model": ("financials", "StatementLine"), "scope": "company_or_global",
        "group": "Finanzas", "name": "Estructura de estados financieros",
        "fields": [
            "statement", "code", "name", "line_type", "formula", "section",
            "display_order", "is_percentage_base",
        ],
    },
    "ratio-definitions": {
        "model": ("financials", "RatioDefinition"), "scope": "global",
        "group": "Finanzas", "name": "Definiciones de ratios",
        "fields": [
            "code", "name", "group", "numerator_formula",
            "denominator_formula", "multiplier", "unit", "recommendation",
            "interpretation", "display_order", "is_active",
        ],
    },
    "ratio-thresholds": {
        "model": ("financials", "RatioThreshold"), "scope": "global",
        "group": "Finanzas", "name": "Umbrales de ratios",
        "fields": ["ratio", "min_value", "max_value", "label", "severity", "advice"],
    },
    "categorization-rules": {
        "model": ("financials", "CategorizationRule"), "scope": "company",
        "group": "Finanzas", "name": "Reglas de categorización",
        "fields": [
            "priority", "name", "is_active", "conditions", "category",
            "confidence",
        ],
    },
    "financial-settings": {
        "model": ("financials", "FinancialSettings"), "scope": "company",
        "group": "Finanzas", "name": "Configuración financiera",
        "fields": [
            "functional_currency", "display_scale", "days_per_month",
            "days_per_year", "use_elapsed_days_for_partial_year",
            "fiscal_year_start_month", "minimum_categorized_pct",
        ],
        "singleton": True,
    },
}


def _entry_or_404(key: str) -> dict:
    if key not in REGISTRY:
        from django.http import Http404

        raise Http404(key)
    return REGISTRY[key]


def _model_for(entry: dict):
    app_label, model_name = entry["model"]
    return django_apps.get_model(app_label, model_name)


def _serializer_for(entry: dict):
    model = _model_for(entry)

    class MasterSerializer(serializers.ModelSerializer):
        class Meta:  # noqa: D106
            fields = ["id", *entry["fields"]]
            read_only_fields = ["id"]

    MasterSerializer.Meta.model = model
    return MasterSerializer


def _field_schema(entry: dict) -> list[dict]:
    """What the generic editor needs to render inputs: name, kind,
    choices, label — derived from the model, never duplicated by hand."""
    model = _model_for(entry)
    schema = []
    for name in entry["fields"]:
        field = model._meta.get_field(name)
        kind = field.get_internal_type()
        schema.append({
            "name": name,
            "label": str(field.verbose_name),
            "kind": kind,
            "required": not (field.null or field.blank or field.has_default()),
            "choices": (
                [{"value": v, "label": str(l)} for v, l in field.choices]
                if getattr(field, "choices", None) else None
            ),
            "help_text": str(field.help_text or ""),
            "is_relation": field.is_relation,
        })
    return schema


def _scoped_queryset(entry: dict, taxpayer_id: str):
    model = _model_for(entry)
    if entry["scope"] == "global":
        return model.objects.all()
    if entry["scope"] == "company":
        return model.objects.filter(taxpayer_id=taxpayer_id)
    return model.objects.filter(taxpayer_id__in=["", taxpayer_id])


class MastersIndexView(ManagedOrganizationAPIView):
    """The catalog the Maestros screen renders its menu from."""

    def get(self, request: Request) -> Response:
        out = []
        for key, entry in REGISTRY.items():
            out.append({
                "key": key,
                "name": entry["name"],
                "group": entry["group"],
                "scope": entry["scope"],
                "singleton": entry.get("singleton", False),
                "count": _scoped_queryset(entry, request.ruc).count(),
            })
        return Response(out)


class MasterListView(ManagedOrganizationAPIView):
    def get(self, request: Request, key: str) -> Response:
        entry = _entry_or_404(key)
        serializer_class = _serializer_for(entry)
        rows = _scoped_queryset(entry, request.ruc)
        if entry.get("singleton") and entry["scope"] == "company":
            # Policy singletons exist lazily with their defaults.
            _model_for(entry).objects.get_or_create(taxpayer_id=request.ruc)
            rows = _scoped_queryset(entry, request.ruc)
        return Response({
            "key": key,
            "name": entry["name"],
            "scope": entry["scope"],
            "fields": _field_schema(entry),
            "rows": serializer_class(rows, many=True).data,
        })

    def post(self, request: Request, key: str) -> Response:
        entry = _entry_or_404(key)
        serializer = _serializer_for(entry)(data=request.data)
        serializer.is_valid(raise_exception=True)
        extra = {}
        if entry["scope"] in ("company", "company_or_global"):
            # New rows always belong to the company; the global seed stays
            # shared and is shadowed, never edited from here.
            extra["taxpayer_id"] = request.ruc
        serializer.save(**extra)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class MasterDetailView(ManagedOrganizationAPIView):
    def _row(self, request: Request, key: str, pk):
        entry = _entry_or_404(key)
        return entry, get_object_or_404(_scoped_queryset(entry, request.ruc), pk=pk)

    def patch(self, request: Request, key: str, pk) -> Response:
        entry, row = self._row(request, key, pk)
        if entry["scope"] == "company_or_global" and row.taxpayer_id == "":
            return Response(
                {"detail": "Las filas del catálogo global no se editan: crea "
                           "una propia con el mismo código y la reemplaza."},
                status=status.HTTP_409_CONFLICT,
            )
        serializer = _serializer_for(entry)(row, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request: Request, key: str, pk) -> Response:
        entry, row = self._row(request, key, pk)
        if entry["scope"] == "company_or_global" and row.taxpayer_id == "":
            return Response(
                {"detail": "Las filas del catálogo global no se eliminan."},
                status=status.HTTP_409_CONFLICT,
            )
        from django.db.models import ProtectedError

        try:
            row.delete()
        except ProtectedError:
            return Response(
                {"detail": "Esta fila ya se usó en cálculos: desactívala en "
                           "lugar de eliminarla."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)
