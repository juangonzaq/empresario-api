"""Payroll API (spec §7–§8).

Everything requires management permission over the company: a payroll is
the most sensitive dataset in the product. Downloads (payslips) go through
authenticated endpoints and validate ``taxpayer_id`` — never a public URL,
with the same criterion already applied to contracts.
"""

from __future__ import annotations

import io
import zipfile

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.models import Organization
from accounts.tenancy import ManagedOrganizationAPIView
from colaboradores.models import Colaborador

from django.utils import timezone

from .models import (
    IncomeTaxMonthlyInput, IncomeTaxProjection, IncomeTaxWithholdingSchedule,
    PayrollEntry, PayrollPeriod, PayrollStatus,
)
from .serializers import (
    AttendanceSerializer, EntryDetailSerializer, EntrySerializer,
    PeriodSerializer, ProjectionSerializer,
)
from .services import income_tax, payslip, runner, validations
from .services.calculator import Line
from .services.master_data import (
    MasterDataMissing, RatesSnapshot, UnknownConcept,
)
from .services.money import D, money


def _period_or_404(request: Request, pk) -> PayrollPeriod:
    return get_object_or_404(PayrollPeriod, pk=pk, taxpayer_id=request.ruc)


def _entry_or_404(request: Request, pk) -> PayrollEntry:
    return get_object_or_404(
        PayrollEntry.objects.select_related("period", "colaborador"),
        pk=pk, period__taxpayer_id=request.ruc,
    )


def _period_payload(request: Request, period: PayrollPeriod) -> dict:
    incidents = validations.validate_period(period)
    entries = period.entries.select_related("colaborador").order_by(
        "colaborador__full_name"
    )
    return {
        **PeriodSerializer(period).data,
        "entries": EntrySerializer(entries, many=True).data,
        "incidents": [incident.as_dict() for incident in incidents],
        "totals": _totals(entries),
    }


def _totals(entries) -> dict:
    fields = (
        "gross_pay", "total_withholdings", "total_deductions", "net_pay",
        "health_contribution", "work_risk_insurance", "total_employer_cost",
    )
    totals = {name: D("0") for name in fields}
    for entry in entries:
        for name in fields:
            totals[name] += D(getattr(entry, name))
    return {name: money(value) for name, value in totals.items()}


class PeriodsView(ManagedOrganizationAPIView):
    """``GET`` lists periods; ``POST {year, month}`` creates one with the
    staff pre-seeded (attendance defaults to the link days, §7.5)."""

    def get(self, request: Request) -> Response:
        periods = PayrollPeriod.objects.filter(taxpayer_id=request.ruc)
        return Response(PeriodSerializer(periods, many=True).data)

    def post(self, request: Request) -> Response:
        try:
            year = int(request.data.get("year"))
            month = int(request.data.get("month"))
        except (TypeError, ValueError):
            return Response(
                {"detail": "Indica año y mes numéricos."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not (1 <= month <= 12 and 2000 <= year <= 2100):
            return Response(
                {"detail": "Periodo fuera de rango."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if PayrollPeriod.objects.filter(
            taxpayer_id=request.ruc, year=year, month=month
        ).exists():
            return Response(
                {"detail": "Ese periodo ya existe."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            period = runner.create_period(request.ruc, year, month)
        except MasterDataMissing as error:  # V16 blocks the start
            return Response(
                {"detail": str(error), "code": "master_data_missing"},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            _period_payload(request, period), status=status.HTTP_201_CREATED
        )


class PeriodView(ManagedOrganizationAPIView):
    def get(self, request: Request, pk) -> Response:
        return Response(_period_payload(request, _period_or_404(request, pk)))

    def delete(self, request: Request, pk) -> Response:
        period = _period_or_404(request, pk)
        if period.is_closed:
            return Response(
                {"detail": "Un periodo cerrado no se elimina: es el registro "
                           "de lo que se pagó."},
                status=status.HTTP_409_CONFLICT,
            )
        period.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class _TransitionView(ManagedOrganizationAPIView):
    """One POST per state change, all funnelled through the runner."""

    action: str = ""

    def post(self, request: Request, pk) -> Response:
        period = _period_or_404(request, pk)
        try:
            if self.action == "calculate":
                runner.calculate_period(period)
            elif self.action == "approve":
                runner.approve_period(period)
            elif self.action == "reopen":
                runner.reopen_period(period)
            elif self.action == "close":
                runner.close_period(
                    period, request.user,
                    accept_warnings=request.data.get("accept_warnings") is True,
                )
        except (runner.TransitionError, runner.PeriodLocked) as error:
            return Response(
                {"detail": str(error)}, status=status.HTTP_409_CONFLICT
            )
        except MasterDataMissing as error:
            return Response(
                {"detail": str(error), "code": "master_data_missing"},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(_period_payload(request, period))


class CalculateView(_TransitionView):
    action = "calculate"


class ApproveView(_TransitionView):
    action = "approve"


class ReopenView(_TransitionView):
    action = "reopen"


class CloseView(_TransitionView):
    action = "close"


class EntryView(ManagedOrganizationAPIView):
    """``GET`` returns the payslip breakdown; ``PATCH`` edits attendance
    and recalculates the row live (§7.1: calculated cells refresh on every
    edit, no reload)."""

    def get(self, request: Request, pk) -> Response:
        return Response(EntryDetailSerializer(_entry_or_404(request, pk)).data)

    def patch(self, request: Request, pk) -> Response:
        entry = _entry_or_404(request, pk)
        if not entry.period.is_editable:
            return Response(
                {"detail": "El periodo ya no se puede editar."},
                status=status.HTTP_409_CONFLICT,
            )
        serializer = AttendanceSerializer(entry, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        runner.recalculate_entry(entry)
        entry.refresh_from_db()
        return Response(EntryDetailSerializer(entry).data)


class EntryManualLinesView(ManagedOrganizationAPIView):
    """``PUT`` replaces the manual concept lines of one entry.

    Body: ``{"lines": [{"code": "BONUS", "amount": "500.00"}, ...]}`` —
    then the row recalculates so the bases and the net absorb the change.
    """

    def put(self, request: Request, pk) -> Response:
        entry = _entry_or_404(request, pk)
        if not entry.period.is_editable:
            return Response(
                {"detail": "El periodo ya no se puede editar."},
                status=status.HTTP_409_CONFLICT,
            )
        raw_lines = request.data.get("lines")
        if not isinstance(raw_lines, list):
            return Response(
                {"detail": "Manda las líneas como lista en «lines»."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        period = entry.period
        rates = RatesSnapshot.resolve(period.taxpayer_id, period.year, period.month)
        parsed: list[Line] = []
        for raw in raw_lines:
            code = str(raw.get("code") or "").strip()
            try:
                concept = rates.concept(code)
            except UnknownConcept:
                return Response(
                    {"detail": f"Concepto desconocido: {code or '(vacío)'}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if concept.is_computed:
                return Response(
                    {"detail": f"{concept.name} lo calcula el motor; no se "
                               "captura a mano."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                amount = money(D(raw.get("amount")))
            except Exception:
                return Response(
                    {"detail": f"Importe no válido en {code}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if amount <= 0:
                return Response(
                    {"detail": f"El importe de {code} debe ser mayor a cero: "
                               "el signo lo aporta el tipo de concepto."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            parsed.append(Line(concept=concept, amount=amount, is_manual=True))

        entry.lines.filter(is_manual=True).delete()
        from .models import PayrollEntryLine

        PayrollEntryLine.objects.bulk_create([
            PayrollEntryLine(
                entry=entry, concept=line.concept, amount=line.amount,
                is_manual=True,
            )
            for line in parsed
        ])
        runner.recalculate_entry(entry)
        entry.refresh_from_db()
        return Response(EntryDetailSerializer(entry).data)


class EntryPayslipView(ManagedOrganizationAPIView):
    """Individual payslip PDF, always through this authenticated endpoint."""

    def get(self, request: Request, pk) -> HttpResponse:
        entry = _entry_or_404(request, pk)
        company = Organization.objects.filter(ruc=request.ruc).first()
        pdf = payslip.render_payslip(entry, company.name if company else "")
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="{payslip.payslip_filename(entry)}"'
        )
        return response


class PeriodPayslipsView(ManagedOrganizationAPIView):
    """Every payslip of the period in one ZIP (§8.1)."""

    def get(self, request: Request, pk) -> HttpResponse:
        period = _period_or_404(request, pk)
        company = Organization.objects.filter(ruc=request.ruc).first()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
            for entry in period.entries.select_related("colaborador"):
                if entry.colaborador.monthly_salary is None:
                    continue
                bundle.writestr(
                    payslip.payslip_filename(entry),
                    payslip.render_payslip(entry, company.name if company else ""),
                )
        response = HttpResponse(
            buffer.getvalue(), content_type="application/zip"
        )
        response["Content-Disposition"] = (
            f'attachment; filename="boletas_{period.year}-{period.month:02d}.zip"'
        )
        return response


def _recalculate_open_entry(taxpayer_id: str, colaborador: Colaborador, year: int) -> None:
    """New tax inputs change the annual tax: redistribute the open months
    right away (§5.5) by recalculating the earliest open period's entry."""
    open_period = PayrollPeriod.objects.filter(
        taxpayer_id=taxpayer_id, year=year,
    ).exclude(status=PayrollStatus.CLOSED).order_by("month").first()
    if open_period is None:
        return
    entry = open_period.entries.filter(colaborador=colaborador).first()
    if entry is not None and colaborador.monthly_salary is not None:
        runner.recalculate_entry(entry)


class EmployeeProjectionView(ManagedOrganizationAPIView):
    """``GET`` the annual income-tax projection; ``PATCH`` the inputs only
    the worker can provide (previous employer, profit sharing).

    ``GET`` creates the projection on demand: the accountant loads the
    historical data BEFORE the first run, so the record must exist before
    the engine ever computed anything.
    """

    def get(self, request: Request, pk, year: int) -> Response:
        colaborador = get_object_or_404(
            Colaborador, pk=pk, taxpayer_id=request.ruc
        )
        projection, _ = IncomeTaxProjection.objects.get_or_create(
            colaborador=colaborador, year=year
        )
        return Response(ProjectionSerializer(projection).data)

    def patch(self, request: Request, pk, year: int) -> Response:
        colaborador = get_object_or_404(
            Colaborador, pk=pk, taxpayer_id=request.ruc
        )
        projection, _ = IncomeTaxProjection.objects.get_or_create(
            colaborador=colaborador, year=year
        )
        serializer = ProjectionSerializer(
            projection, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        _recalculate_open_entry(request.ruc, colaborador, year)
        projection.refresh_from_db()
        return Response(ProjectionSerializer(projection).data)


class EmployeeTaxMonthlyInputsView(ManagedOrganizationAPIView):
    """``PUT`` replaces the loaded monthly history of one employee's year.

    Body: ``{"months": [{"month": 1, "taxable_income": "6000.00",
    "withheld": "0.00"}, ...]}`` — the actual amounts of the months the
    engine never ran (a mid-year start). Months already closed by the
    engine are rejected: the engine's own record wins there.
    """

    def put(self, request: Request, pk, year: int) -> Response:
        colaborador = get_object_or_404(
            Colaborador, pk=pk, taxpayer_id=request.ruc
        )
        raw_months = request.data.get("months")
        if not isinstance(raw_months, list):
            return Response(
                {"detail": "Manda los meses como lista en «months»."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        closed_months = set(
            PayrollEntry.objects.filter(
                colaborador=colaborador,
                period__year=year,
                period__status=PayrollStatus.CLOSED,
            ).values_list("period__month", flat=True)
        )
        parsed: list[IncomeTaxMonthlyInput] = []
        seen: set[int] = set()
        for raw in raw_months:
            try:
                month = int(raw.get("month"))
                taxable = money(D(raw.get("taxable_income", "0") or "0"))
                withheld = money(D(raw.get("withheld", "0") or "0"))
            except Exception:
                return Response(
                    {"detail": "Mes o importe no válido en la lista."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not 1 <= month <= 12 or month in seen:
                return Response(
                    {"detail": f"Mes fuera de rango o repetido: {month}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if taxable < 0 or withheld < 0:
                return Response(
                    {"detail": f"Los importes del mes {month} no pueden ser "
                               "negativos."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if month in closed_months:
                return Response(
                    {"detail": f"El mes {month} ya lo calculó y cerró el "
                               "sistema: su registro no se sobreescribe."},
                    status=status.HTTP_409_CONFLICT,
                )
            seen.add(month)
            parsed.append(IncomeTaxMonthlyInput(
                colaborador=colaborador, year=year, month=month,
                taxable_income=taxable, withheld=withheld,
                note=str(raw.get("note", "") or "")[:200],
            ))

        colaborador.income_tax_monthly_inputs.filter(year=year).delete()
        IncomeTaxMonthlyInput.objects.bulk_create(parsed)
        _recalculate_open_entry(request.ruc, colaborador, year)
        projection, _ = IncomeTaxProjection.objects.get_or_create(
            colaborador=colaborador, year=year
        )
        return Response(ProjectionSerializer(projection).data)


class EntryTaxOverrideView(ManagedOrganizationAPIView):
    """``POST`` pins (or clears) the month's income-tax withholding.

    Body: ``{"amount": "1500.00", "reason": "…"}`` to pin — the reason is
    mandatory, the adjustment is audited — or ``{"amount": null}`` to go
    back to the engine's figure. The rest of the year redistributes.
    """

    def post(self, request: Request, pk) -> Response:
        entry = _entry_or_404(request, pk)
        period = entry.period
        if not period.is_editable:
            return Response(
                {"detail": "El periodo ya no se puede editar."},
                status=status.HTTP_409_CONFLICT,
            )
        raw_amount = request.data.get("amount", None)
        if raw_amount is None:
            override = None
        else:
            try:
                override = money(D(raw_amount))
            except Exception:
                return Response(
                    {"detail": "Importe no válido."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if override < 0:
                return Response(
                    {"detail": "La retención ajustada no puede ser negativa."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        reason = str(request.data.get("reason", "") or "").strip()
        if override is not None and not reason:
            return Response(
                {"detail": "El ajuste exige un motivo: es lo que lo hace "
                           "auditable."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        projection, _ = IncomeTaxProjection.objects.get_or_create(
            colaborador=entry.colaborador, year=period.year
        )
        row, _ = IncomeTaxWithholdingSchedule.objects.get_or_create(
            projection=projection, month=period.month
        )
        if row.is_settled:
            return Response(
                {"detail": "Un mes liquidado no se ajusta."},
                status=status.HTTP_409_CONFLICT,
            )
        row.override_amount = override
        row.override_reason = reason if override is not None else ""
        row.overridden_by = (
            request.user
            if override is not None and request.user.is_authenticated else None
        )
        row.overridden_at = timezone.now() if override is not None else None
        row.save(update_fields=[
            "override_amount", "override_reason", "overridden_by",
            "overridden_at", "updated_at",
        ])
        runner.recalculate_entry(entry)
        entry.refresh_from_db()
        return Response(EntryDetailSerializer(entry).data)


class TaxAnnualView(ManagedOrganizationAPIView):
    """``GET /api/payroll/tax-annual/{year}/`` — the accountant's annual
    matrix: one row per active employee with the projected income month
    by month, the deduction, the bracket split, the annual tax and the
    monthly withholding schedule. It reads what the engine last computed;
    it never recalculates."""

    def get(self, request: Request, year: int) -> Response:
        colaboradores = Colaborador.objects.filter(
            taxpayer_id=request.ruc, is_active=True,
        ).order_by("full_name")
        projections = {
            p.colaborador_id: p
            for p in IncomeTaxProjection.objects.filter(
                colaborador__taxpayer_id=request.ruc, year=year,
            ).prefetch_related("schedule")
        }
        rows = [
            self._row(person, projections.get(person.pk))
            for person in colaboradores
        ]
        return Response({"year": year, "rows": rows})

    @staticmethod
    def _row(person: Colaborador, projection) -> dict:
        base = {
            "colaborador_id": str(person.pk),
            "full_name": person.full_name,
            "document_number": person.document_number,
            "has_eps": person.has_eps,
            "has_projection": projection is not None,
        }
        if projection is None:
            return base
        detail = projection.computation_detail or {}
        total_income = (
            D(projection.projected_annual_income)
            + D(projection.previous_employer_income)
            + D(projection.profit_sharing)
        )
        return {
            **base,
            "monthly_income": detail.get("monthly_income", {}),
            "current_month": detail.get("current_month"),
            "previous_employer_income": projection.previous_employer_income,
            "profit_sharing": projection.profit_sharing,
            "total_income": money(total_income),
            "standard_deduction": detail.get("standard_deduction"),
            "taxable_income": projection.taxable_income,
            "bracket_detail": projection.bracket_detail,
            "annual_tax": projection.annual_tax,
            "over_withheld": detail.get("over_withheld", False),
            "schedule": [
                {
                    "month": row.month,
                    "effective_amount": row.effective_amount,
                    "is_settled": row.is_settled,
                    "is_overridden": row.override_amount is not None,
                }
                for row in projection.schedule.all()
            ],
            "recalculated_at": projection.recalculated_at,
        }


class EmployeePayslipsView(ManagedOrganizationAPIView):
    """``GET /api/payroll/employees/{id}/payslips/?year=&month=`` — every
    payslip of one employee across periods, newest first.

    The payslip belongs to the person, not only to the period screen: HR
    answers "dame la boleta de Juana de mayo" from the employee's record,
    filtering by month, without opening period by period.
    """

    def get(self, request: Request, pk) -> Response:
        colaborador = get_object_or_404(
            Colaborador, pk=pk, taxpayer_id=request.ruc
        )
        entries = (
            PayrollEntry.objects.filter(
                colaborador=colaborador, period__taxpayer_id=request.ruc
            )
            .select_related("period")
            .order_by("-period__year", "-period__month")
        )
        year = request.query_params.get("year")
        month = request.query_params.get("month")
        if year and year.isdigit():
            entries = entries.filter(period__year=int(year))
        if month and month.isdigit():
            entries = entries.filter(period__month=int(month))

        months_es = [
            "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "septiembre", "octubre", "noviembre", "diciembre",
        ]
        return Response({
            "colaborador_id": str(colaborador.pk),
            "full_name": colaborador.full_name,
            "years": sorted(
                {e.period.year for e in PayrollEntry.objects.filter(
                    colaborador=colaborador, period__taxpayer_id=request.ruc
                ).select_related("period")},
                reverse=True,
            ),
            "payslips": [
                {
                    "entry_id": str(entry.pk),
                    "year": entry.period.year,
                    "month": entry.period.month,
                    "label": f"{months_es[entry.period.month - 1]} {entry.period.year}",
                    "period_status": entry.period.status,
                    "gross_pay": entry.gross_pay,
                    "net_pay": entry.net_pay,
                    "total_employer_cost": entry.total_employer_cost,
                    "has_amounts": entry.colaborador.monthly_salary is not None
                    or entry.gross_pay > 0,
                }
                for entry in entries
            ],
        })
