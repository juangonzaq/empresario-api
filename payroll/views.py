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

from .models import PayrollEntry, PayrollPeriod
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


class EmployeeProjectionView(ManagedOrganizationAPIView):
    """``GET`` the annual income-tax projection; ``PATCH`` the inputs only
    the worker can provide (previous employer, profit sharing)."""

    def get(self, request: Request, pk, year: int) -> Response:
        colaborador = get_object_or_404(
            Colaborador, pk=pk, taxpayer_id=request.ruc
        )
        projection = get_object_or_404(
            colaborador.income_tax_projections, year=year
        )
        return Response(ProjectionSerializer(projection).data)

    def patch(self, request: Request, pk, year: int) -> Response:
        colaborador = get_object_or_404(
            Colaborador, pk=pk, taxpayer_id=request.ruc
        )
        projection = get_object_or_404(
            colaborador.income_tax_projections, year=year
        )
        serializer = ProjectionSerializer(
            projection, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        # New external inputs change the annual tax: redistribute the open
        # months right away (§5.5).
        open_period = PayrollPeriod.objects.filter(
            taxpayer_id=request.ruc, year=year,
        ).exclude(status="closed").order_by("month").first()
        if open_period is not None:
            entry = open_period.entries.filter(colaborador=colaborador).first()
            if entry is not None and colaborador.monthly_salary is not None:
                runner.recalculate_entry(entry)
        projection.refresh_from_db()
        return Response(ProjectionSerializer(projection).data)


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
