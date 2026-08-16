"""Serializers for the payroll API. Read-heavy: the engine writes the
numbers, the client only ever writes attendance, manual lines and the
extra income-tax inputs."""

from __future__ import annotations

from rest_framework import serializers

from .models import (
    IncomeTaxProjection, PayrollEntry, PayrollEntryLine, PayrollPeriod,
)


class EntryLineSerializer(serializers.ModelSerializer):
    code = serializers.CharField(source="concept.code", read_only=True)
    name = serializers.CharField(source="concept.name", read_only=True)
    kind = serializers.CharField(source="concept.kind", read_only=True)
    display_order = serializers.IntegerField(
        source="concept.display_order", read_only=True
    )

    class Meta:
        model = PayrollEntryLine
        fields = (
            "id", "code", "name", "kind", "display_order", "quantity",
            "amount", "is_manual",
        )


class EntrySerializer(serializers.ModelSerializer):
    colaborador_id = serializers.CharField(source="colaborador.pk", read_only=True)
    full_name = serializers.CharField(source="colaborador.full_name", read_only=True)
    document_number = serializers.CharField(
        source="colaborador.document_number", read_only=True
    )
    position = serializers.CharField(source="colaborador.position", read_only=True)
    has_salary = serializers.SerializerMethodField()

    class Meta:
        model = PayrollEntry
        fields = (
            "id", "colaborador_id", "full_name", "document_number", "position",
            "base_salary", "pension_regime", "pension_fund", "commission_type",
            "cuspp", "has_salary",
            # attendance (writable through the dedicated PATCH view)
            "worked_days", "vacation_days", "sick_leave_days", "leave_days",
            "absence_days", "first_overtime_hours", "additional_overtime_hours",
            # computed
            "pension_base", "income_tax_base", "gross_pay",
            "pension_contribution", "pension_commission", "pension_insurance",
            "income_tax_withholding", "total_withholdings", "total_deductions",
            "net_pay", "health_contribution", "work_risk_insurance",
            "total_employer_cost", "total_hours", "has_warnings",
        )
        read_only_fields = fields

    def get_has_salary(self, entry: PayrollEntry) -> bool:
        return entry.colaborador.monthly_salary is not None


class EntryDetailSerializer(EntrySerializer):
    lines = EntryLineSerializer(many=True, read_only=True)
    rates_snapshot = serializers.JSONField(read_only=True)

    class Meta(EntrySerializer.Meta):
        fields = (*EntrySerializer.Meta.fields, "lines", "rates_snapshot")
        read_only_fields = fields


class AttendanceSerializer(serializers.ModelSerializer):
    """The only writable surface of an entry: attendance counters."""

    class Meta:
        model = PayrollEntry
        fields = (
            "worked_days", "vacation_days", "sick_leave_days", "leave_days",
            "absence_days", "first_overtime_hours", "additional_overtime_hours",
        )
        extra_kwargs = {
            **{name: {"required": False} for name in fields},
            "first_overtime_hours": {"required": False, "min_value": 0},
            "additional_overtime_hours": {"required": False, "min_value": 0},
        }


class PeriodSerializer(serializers.ModelSerializer):
    label = serializers.SerializerMethodField()
    entry_count = serializers.IntegerField(source="entries.count", read_only=True)

    class Meta:
        model = PayrollPeriod
        fields = (
            "id", "year", "month", "label", "status", "tax_unit_amount",
            "minimum_wage_amount", "calculated_at", "approved_at", "closed_at",
            "entry_count",
        )
        read_only_fields = fields

    def get_label(self, period: PayrollPeriod) -> str:
        months = [
            "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "septiembre", "octubre", "noviembre", "diciembre",
        ]
        return f"{months[period.month - 1]} {period.year}"


class ProjectionSerializer(serializers.ModelSerializer):
    schedule = serializers.SerializerMethodField()

    class Meta:
        model = IncomeTaxProjection
        fields = (
            "year", "projected_annual_income", "previous_employer_income",
            "previous_employer_withheld", "profit_sharing", "taxable_income",
            "annual_tax", "bracket_detail", "schedule", "recalculated_at",
        )
        read_only_fields = (
            "year", "projected_annual_income", "taxable_income", "annual_tax",
            "bracket_detail", "schedule", "recalculated_at",
        )

    def get_schedule(self, projection: IncomeTaxProjection) -> list[dict]:
        return [
            {"month": row.month, "amount": row.amount, "is_settled": row.is_settled}
            for row in projection.schedule.all()
        ]
