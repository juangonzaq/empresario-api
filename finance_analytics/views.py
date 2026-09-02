"""API for the executive finance analytics (CPE + ITF).

Read endpoints aggregate over the FULL dataset (never one page), and every
amount is grouped by currency. The raw CPE/ITF APIs stay available for audit.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from sunat_cpe.models import Direction, ElectronicInvoice

from accounts.tenancy import ManagedOrganizationAPIView, OrganizationAPIView

from . import cache as overview_cache

from .models import (
    SEVERITY_RANK, ActionStatus, AlertStatus, FinanceAlert, InvoiceOverride,
    ManualEntry,
)
from .services import ai_summary as ai_service
from .services import consistency as consistency_service
from .services import itf_summary as itf_service
from .services import parties as parties_service
from .services import renta as renta_service
from .services import semaforo as semaforo_service
from .services.common import clean_name, money
from .services.cpe_summary import (
    credit_notes_detail, document_amount, document_is_edited, load_documents,
    load_manual_entries, manual_entry_payload, period_documents_for,
    purchases_summary, sales_summary,
)

logger = logging.getLogger(__name__)


class SalesView(OrganizationAPIView):
    def get(self, request: Request) -> Response:
        return Response(
            sales_summary(
                load_documents(request.ruc), manual=load_manual_entries(request.ruc)
            )
        )


class PurchasesView(OrganizationAPIView):
    def get(self, request: Request) -> Response:
        return Response(
            purchases_summary(
                load_documents(request.ruc), manual=load_manual_entries(request.ruc)
            )
        )


class CustomersView(OrganizationAPIView):
    def get(self, request: Request) -> Response:
        return Response(parties_service.customers_analysis(load_documents(request.ruc)))


class CreditNotesView(OrganizationAPIView):
    def get(self, request: Request) -> Response:
        return Response(credit_notes_detail(load_documents(request.ruc)))


class PeriodDocumentsView(OrganizationAPIView):
    """The comprobantes behind one row of the monthly table.

    ``GET /api/finance/documents/?period=202607&direction=emitida&currency=PEN``
    """

    def get(self, request: Request) -> Response:
        period = (request.query_params.get("period") or "").strip()
        direction = request.query_params.get("direction") or Direction.ISSUED
        currency = (request.query_params.get("currency") or "").strip() or None

        if not (len(period) == 6 and period.isdigit()):
            return Response(
                {"detail": "Indica el periodo como aaaamm."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if direction not in Direction.values:
            return Response(
                {"detail": "Dirección no válida."}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(
            period_documents_for(request.ruc, period, direction, currency)
        )


class ItfView(OrganizationAPIView):
    """``?months=`` amplía la ventana (tope 120): la vista de ITF navega por
    año con el selector de periodo y necesita el histórico completo. Son
    agregados por mes, no filas: pedir 10 años sigue siendo barato."""

    def get(self, request: Request) -> Response:
        try:
            months = int(request.query_params.get("months") or 12)
        except (TypeError, ValueError):
            months = 12
        return Response(
            itf_service.itf_summary(request.ruc, months=max(12, min(120, months)))
        )


class OverviewView(OrganizationAPIView):
    """Una sola llamada para los cards del Home y la pestaña Resumen.

    Se cachea por empresa. Antes esta vista además *reconstruía las alertas*
    en cada GET: 19 escrituras por cada carga de pantalla, con la base
    aguantando la contención y el endpoint imposible de cachear. Las alertas
    se recalculan donde corresponde —al terminar una sincronización, que es
    cuando los datos cambian— y aquí solo se leen.
    """

    # La ventana de las series se puede ampliar (?months=) para que el tablero
    # muestre el resumen de un año antiguo. Con tope: el histórico entero de
    # una empresa grande costaba decenas de MB por petición.
    MIN_MONTHS = 13
    MAX_MONTHS = 120

    def _months(self, request: Request) -> int:
        try:
            months = int(request.query_params.get("months") or self.MIN_MONTHS)
        except (TypeError, ValueError):
            months = self.MIN_MONTHS
        return max(self.MIN_MONTHS, min(self.MAX_MONTHS, months))

    def get(self, request: Request) -> Response:
        months = self._months(request)
        cached = overview_cache.get_overview(request.ruc, months)
        if cached is not None:
            return Response(cached)
        return Response(self._build(request, months))

    def _build(self, request: Request, months: int = MIN_MONTHS) -> dict:
        from .services.cpe_summary import _period_floor

        docs_series = load_documents(request.ruc, months)
        manual_series = load_manual_entries(request.ruc, months)
        # Solo las series de ventas/compras/ITF viajan en el tiempo. Clientes,
        # consistencia, semáforo y briefing siguen mirando los últimos 13
        # meses: sus ventanas y su costo están calibrados para eso.
        if months > self.MIN_MONTHS:
            floor = _period_floor(self.MIN_MONTHS)
            docs = [d for d in docs_series if d.period >= floor]
        else:
            docs = docs_series
        sales = sales_summary(docs_series, months=months, manual=manual_series)
        purchases = purchases_summary(docs_series, months=months, manual=manual_series)
        customers = parties_service.customers_analysis(docs)
        itf = itf_service.itf_summary(request.ruc, months=max(months, 12))
        consistency = consistency_service.consistency_analysis(docs, request.ruc)
        open_alerts = sorted(
            FinanceAlert.objects.filter(account_ruc=request.ruc).open(),
            key=lambda a: (SEVERITY_RANK.get(a.severity, 9), a.period),
        )
        priority = [a for a in open_alerts if SEVERITY_RANK.get(a.severity, 9) <= 1]

        period = sales.get("latest_period")
        row = ai_service.latest_summary(request.ruc, period) if period else None
        cached_ai = ai_service.payload(row) if ai_service.has_briefing(row) else None

        payload = {
            "period": period,
            "sales": {
                "meaning": sales["meaning"],
                "current": sales["current"],
                "previous": sales["previous"],
                "series": sales["periods"],
            },
            "purchases": {"current": purchases["current"], "series": purchases["periods"]},
            # Semáforo de gastos sobre ingresos: personal (planilla), otros
            # gastos (compras + manuales) y el total, en % del mes.
            "semaforo": semaforo_service.semaforo(request.ruc, sales, purchases),
            "customers": customers["summary"],
            "top_customers": customers["parties"][:5],
            # El cliente principal de cada año cubierto por la ventana pedida:
            # el card del tablero lo usa para seguir al año elegido.
            "customers_yearly": parties_service.top_customers_by_year(docs_series),
            "itf": {
                "meaning": itf["meaning"],
                "gross_movement_note": itf["gross_movement_note"],
                "current": itf["current"],
                "previous": itf.get("previous"),
                "banks": itf["banks"],
                "catalog_note": itf.get("catalog_note"),
                "series": itf["periods"],
            },
            "consistency": {
                "status": consistency["status"],
                "methodology": consistency["methodology"],
                "not_a_breach_note": consistency["not_a_breach_note"],
                "findings": consistency["findings"],
                "review_findings": consistency["review_findings"],
                "informational_findings": consistency["informational_findings"],
                "rows": consistency["rows"],
                "no_overlap_note": consistency["no_overlap_note"],
            },
            # El card del Home muestra: total de hallazgos, cuántos son
            # prioritarios y la causa concreta del más grave.
            "alerts": {
                "open": len(open_alerts),
                "total": len(open_alerts),
                "priority": len(priority),
                "main_cause": open_alerts[0].title if open_alerts else None,
                "main_cause_detail": open_alerts[0].explanation if open_alerts else None,
                "main_cause_source": open_alerts[0].source if open_alerts else None,
                "top": [_alert_payload(a) for a in open_alerts[:5]],
            },
            # El 621 del mes según SUNAT: presentado o no, cuándo y cuánto se
            # pagó. Por periodo, para que el selector de mes del tablero lo
            # acompañe.
            "declaraciones": _declaraciones_overview(request.ruc),
            "ai_summary": cached_ai,
            # De dónde salen los números del briefing; se calculan aquí, nunca
            # los redacta el modelo.
            "sources": ai_service.briefing_sources(docs, itf, len(open_alerts), request.ruc),
        }
        overview_cache.set_overview(request.ruc, payload, months)
        return payload


def _declaraciones_overview(ruc: str) -> dict:
    from sunat_declaraciones.services import resumen as declaraciones_resumen

    data = declaraciones_resumen(ruc)
    por_periodo = {}
    for p in data["periodos"]:
        if p["periodo"].endswith("13"):
            continue
        d = p["igv_renta"]
        por_periodo[p["periodo"]] = {
            "presentado": d is not None,
            "fecha_presentacion": d["fecha_presentacion"] if d else None,
            "a_tiempo": d["a_tiempo"] if d else None,
            "vencimiento": p["vencimiento"],
            "pago_a_cuenta": (p["igv_renta_declarado"] or {}).get("renta_pago_a_cuenta"),
            "igv_a_pagar": (p["igv_renta_declarado"] or {}).get("igv_a_pagar"),
            "total_pagado": p["total_pagado"],
        }
    return {
        "disponible": bool(por_periodo),
        "periodo_que_toca": data["periodo_que_toca"],
        "ultima_consulta": data["ultima_consulta"]["fecha"] if data["ultima_consulta"] else None,
        "pagado_12m": data["totales"]["pagado_12m"],
        "por_periodo": por_periodo,
    }


def _alert_payload(alert: FinanceAlert) -> dict:
    return {
        "id": str(alert.id),
        "alert_type": alert.alert_type,
        "severity": alert.severity,
        "period": alert.period,
        "title": alert.title,
        "explanation": alert.explanation,
        "amount": money(alert.amount),
        "currency": alert.currency,
        "source": alert.source,
        "recommendation": alert.recommendation,
        "status": alert.status,
        "is_priority": SEVERITY_RANK.get(alert.severity, 9) <= 1,
    }


class AlertsView(OrganizationAPIView):
    def get(self, request: Request) -> Response:
        alerts = FinanceAlert.objects.filter(account_ruc=request.ruc)
        if request.query_params.get("open") == "true":
            alerts = alerts.open()
        ordered = sorted(
            alerts, key=lambda a: (SEVERITY_RANK.get(a.severity, 9), a.period)
        )
        return Response([_alert_payload(a) for a in ordered])


class AlertStatusView(ManagedOrganizationAPIView):
    def patch(self, request: Request, pk: str) -> Response:
        alert = get_object_or_404(
            FinanceAlert, pk=pk, account_ruc=request.ruc
        )
        new_status = request.data.get("status")
        if new_status not in AlertStatus.values:
            return Response(
                {"detail": "Estado no válido."}, status=status.HTTP_400_BAD_REQUEST
            )
        alert.status = new_status
        alert.save(update_fields=["status", "updated_at"])
        overview_cache.invalidate(request.ruc)
        return Response(_alert_payload(alert))


class _InvalidField(Exception):
    """Un campo del cuerpo no pasó la validación; el mensaje va al cliente."""


def _parse_amount(raw, *, minimum_exclusive: bool = True) -> "Decimal":
    from decimal import Decimal, InvalidOperation

    try:
        amount = Decimal(str(raw)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise _InvalidField("Monto no válido.")
    if amount >= Decimal("1e14") or amount < 0 or (minimum_exclusive and amount == 0):
        raise _InvalidField("El monto debe ser mayor a cero.")
    return amount


def _parse_direction(raw) -> dict:
    if raw not in Direction.values:
        raise _InvalidField("Indica si es ingreso (emitida) o gasto (recibida).")
    return {"direction": raw}


def _parse_entry_date(raw) -> dict:
    import datetime

    try:
        entry_date = datetime.date.fromisoformat(str(raw or ""))
    except ValueError:
        raise _InvalidField("Indica la fecha como aaaa-mm-dd.")
    return {
        "entry_date": entry_date,
        "period": f"{entry_date.year}{entry_date.month:02d}",
    }


def _parse_description(raw) -> dict:
    description = str(raw or "").strip()
    if not description:
        raise _InvalidField("Describe el registro.")
    return {"description": description[:200]}


def _parse_currency(raw) -> dict:
    currency = str(raw or "PEN").strip().upper() or "PEN"
    if len(currency) != 3 or not currency.isalpha():
        raise _InvalidField("Moneda no válida.")
    return {"currency": currency}


_MANUAL_REQUIRED = {
    "direction": _parse_direction,
    "entry_date": _parse_entry_date,
    "description": _parse_description,
}


def _validate_manual_fields(data: dict, *, partial: bool) -> tuple[dict, str | None]:
    """Valida el cuerpo de un registro manual. Con ``partial`` solo se
    validan las claves presentes (PATCH); sin él, todas son obligatorias."""
    fields: dict = {}
    try:
        for key, parse in _MANUAL_REQUIRED.items():
            if key in data or not partial:
                fields.update(parse(data.get(key)))
        if "amount" in data or not partial:
            fields["amount"] = _parse_amount(data.get("amount"))
        if "currency" in data:
            fields.update(_parse_currency(data.get("currency")))
        if "counterparty" in data:
            fields["counterparty"] = str(data.get("counterparty") or "").strip()[:200]
        if "note" in data:
            fields["note"] = str(data.get("note") or "").strip()
        if "category_code" in data:
            fields["category_code"] = _parse_category_code(data.get("category_code"))
    except _InvalidField as error:
        return {}, str(error)
    return fields, None


def _parse_category_code(raw) -> str:
    """Optional: coding the voucher at capture, like an accountant does.
    Empty means «lo decido después» and the movement waits in Categorizar."""
    code = str(raw or "").strip()
    if not code:
        return ""
    from financials.models import TransactionCategory

    exists = TransactionCategory.objects.filter(
        code=code, is_active=True
    ).exists()
    if not exists:
        raise _InvalidField(f"Categoría desconocida: {code}.")
    return code


class ManualEntriesView(ManagedOrganizationAPIView):
    """Registros manuales de ingresos y gastos.

    Se listan y crean aquí; ya vienen incluidos en los totales de ventas,
    compras y en el detalle de cada mes. La sincronización con SUNAT nunca
    los toca.
    """

    def get(self, request: Request) -> Response:
        entries = ManualEntry.objects.filter(account_ruc=request.ruc)
        period = (request.query_params.get("period") or "").strip()
        if period:
            entries = entries.filter(period=period)
        direction = (request.query_params.get("direction") or "").strip()
        if direction:
            entries = entries.filter(direction=direction)
        return Response([manual_entry_payload(e) for e in entries])

    def post(self, request: Request) -> Response:
        fields, error = _validate_manual_fields(request.data, partial=False)
        if error:
            return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)
        entry = ManualEntry.objects.create(account_ruc=request.ruc, **fields)
        _sync_entry_to_financials(entry)
        overview_cache.invalidate(request.ruc)
        return Response(manual_entry_payload(entry), status=status.HTTP_201_CREATED)


def _sync_entry_to_financials(entry: ManualEntry) -> None:
    """A saved record must reach the income statement right away, not
    when someone remembers the sync button. Fail-safe on purpose: the
    record itself is already saved and the bulk sync can catch up."""
    try:
        from financials.services import ingest

        ingest.sync_manual_entry(entry)
    except Exception:  # pragma: no cover — defensive
        logger.exception("manual entry → financials sync failed")


class ManualEntryView(ManagedOrganizationAPIView):
    def patch(self, request: Request, pk: str) -> Response:
        entry = get_object_or_404(ManualEntry, pk=pk, account_ruc=request.ruc)
        fields, error = _validate_manual_fields(request.data, partial=True)
        if error:
            return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)
        for name, value in fields.items():
            setattr(entry, name, value)
        entry.save(update_fields=[*fields, "updated_at"])
        _sync_entry_to_financials(entry)
        overview_cache.invalidate(request.ruc)
        return Response(manual_entry_payload(entry))

    def delete(self, request: Request, pk: str) -> Response:
        entry = get_object_or_404(ManualEntry, pk=pk, account_ruc=request.ruc)
        entry_pk = str(entry.pk)
        entry.delete()
        try:
            from financials.services import ingest

            ingest.remove_manual_entry(request.ruc, entry_pk)
        except Exception:  # pragma: no cover — defensive
            logger.exception("manual entry → financials removal failed")
        overview_cache.invalidate(request.ruc)
        return Response(status=status.HTTP_204_NO_CONTENT)


class InvoiceOverrideView(ManagedOrganizationAPIView):
    """Corrección manual de un comprobante extraído de SUNAT.

    PATCH crea o actualiza la corrección; DELETE la elimina y el comprobante
    vuelve a mostrar los valores de SUNAT. El original nunca se toca, así
    que ninguna sincronización chanca lo editado.
    """

    def patch(self, request: Request, pk: str) -> Response:
        invoice = get_object_or_404(
            ElectronicInvoice, pk=pk, account_ruc=request.ruc
        )
        data = request.data
        fields: dict = {}

        if "total_amount" in data:
            raw = data.get("total_amount")
            if raw is None or raw == "":
                fields["total_amount"] = None
            else:
                try:
                    fields["total_amount"] = _parse_amount(raw, minimum_exclusive=False)
                except _InvalidField as error:
                    return Response(
                        {"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST
                    )
        if "counterparty" in data:
            fields["counterparty"] = str(data.get("counterparty") or "").strip()[:200]
        if "note" in data:
            fields["note"] = str(data.get("note") or "").strip()

        if not fields:
            return Response(
                {"detail": "No hay nada que corregir."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        override, _ = InvoiceOverride.objects.update_or_create(
            invoice=invoice,
            defaults={"account_ruc": request.ruc, **fields},
        )
        if override.total_amount is None and not override.counterparty and not override.note:
            # Quedó vacía: mejor no dejar una corrección fantasma.
            override.delete()
            override = None
        overview_cache.invalidate(request.ruc)
        return Response(_override_payload(override))

    def delete(self, request: Request, pk: str) -> Response:
        invoice = get_object_or_404(
            ElectronicInvoice, pk=pk, account_ruc=request.ruc
        )
        InvoiceOverride.objects.filter(invoice=invoice).delete()
        overview_cache.invalidate(request.ruc)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _override_payload(override: InvoiceOverride | None) -> dict | None:
    if override is None:
        return None
    return {
        "total_amount": money(override.total_amount),
        "counterparty": override.counterparty,
        "note": override.note,
        "updated_at": override.updated_at,
    }


class InvoiceInsightView(OrganizationAPIView):
    """Normalized detail of one comprobante for the UI drawer.

    Never returns the full XML; the raw document stays on the audit API
    (``/api/cpe/invoices/{id}/``).
    """

    def get(self, request: Request, pk: str) -> Response:
        invoice = get_object_or_404(
            ElectronicInvoice.objects.select_related("extract", "override"),
            pk=pk, account_ruc=request.ruc,
        )
        extract = getattr(invoice, "extract", None)
        override = getattr(invoice, "override", None)
        return Response({
            # Corrección manual, si existe: sobrevive a las sincronizaciones
            # y `effective_total` ya la tiene aplicada.
            "override": _override_payload(override),
            "edited": document_is_edited(invoice),
            "effective_total": money(document_amount(invoice)),
            "id": str(invoice.id),
            "direction": invoice.direction,
            "document_class": invoice.document_class,
            "full_number": invoice.full_number or f"{invoice.series}-{invoice.number}",
            "issue_date": invoice.issue_date,
            "period": invoice.period,
            "status": invoice.status,
            "is_cancelled": invoice.is_cancelled,
            "is_rejected": invoice.is_rejected,
            "issuer_ruc": invoice.issuer_ruc,
            "issuer_name": clean_name(invoice.issuer_name),
            "receiver_ruc": invoice.receiver_ruc,
            "receiver_name": clean_name(invoice.receiver_name),
            "currency": invoice.currency or "PEN",
            "total_amount": money(invoice.total_amount),
            "references_document": invoice.references_document,
            "extract": None if extract is None else {
                "status": extract.status,
                "taxable_amount": money(extract.taxable_amount),
                "igv_amount": money(extract.igv_amount),
                "total_amount": money(extract.total_amount),
                "payment_form": extract.payment_form,
                "installments": extract.installments,
                "detraction": extract.detraction,
                "reference_id": extract.reference_id,
                "reference_reason": extract.reference_reason,
                "items": extract.items,
                "due_date": extract.due_date,
                "order_reference": extract.order_reference,
                "supplier_address": extract.supplier_address,
                "customer_address": extract.customer_address,
                "notes": extract.notes,
            },
        })


class AiSummaryView(ManagedOrganizationAPIView):
    """POST — generate (or refresh) the cached monthly briefing.

    A failed generation is not a dead end: if a previous briefing exists it
    comes back marked ``stale`` with a note, so the UI can keep showing the
    last good reading instead of an empty panel.
    """

    def post(self, request: Request) -> Response:
        period = sales_summary(load_documents(request.ruc, months=2), months=1).get("latest_period")
        if not period:
            return Response(
                {"detail": "Aún no hay facturación registrada para este briefing."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            row = ai_service.get_or_create_summary(
                request.ruc, period, force=request.data.get("force") is True
            )
        except Exception:  # el usuario recibe un error amable; el log, el resto
            logger.exception("Finance briefing generation failed")
            previous = ai_service.latest_summary(request.ruc)
            if not ai_service.has_briefing(previous):
                return Response(
                    {"detail": "No pudimos generar el briefing. Intenta de nuevo."},
                    status=status.HTTP_502_BAD_GATEWAY,
                )
            return Response({
                **ai_service.payload(previous),
                "stale": True,
                "stale_note": (
                    "No pudimos actualizar el briefing; estás viendo el último "
                    "disponible."
                ),
            })
        overview_cache.invalidate(request.ruc)
        return Response({**ai_service.payload(row), "stale": False, "stale_note": None})


class AiSummaryActionView(ManagedOrganizationAPIView):
    """PATCH — move one briefing action to another status.

    The status belongs to the person, not to the model: regenerating the
    briefing carries it over by action id.
    """

    def patch(self, request: Request, action_id: str) -> Response:
        new_status = request.data.get("status")
        if new_status not in ActionStatus.values:
            return Response(
                {"detail": "Estado no válido."}, status=status.HTTP_400_BAD_REQUEST
            )
        row = ai_service.latest_summary(request.ruc)
        if row is None or not row.set_action_status(action_id, new_status):
            return Response(
                {"detail": "Acción no encontrada."}, status=status.HTTP_404_NOT_FOUND
            )
        overview_cache.invalidate(request.ruc)
        return Response(ai_service.payload(row))


class RentaView(OrganizationAPIView):
    """Estimador del Impuesto a la Renta del año (``?year=``), según el régimen
    declarado por la empresa. Referencial: no sustituye la contabilidad."""

    def get(self, request: Request) -> Response:
        try:
            year = int(request.query_params.get("year") or timezone.localdate().year)
        except ValueError:
            return Response({"detail": "Indica el año como aaaa."}, status=status.HTTP_400_BAD_REQUEST)
        manual = load_manual_entries(request.ruc)
        data = renta_service.renta_summary(
            request.ruc, request.organization.tax_regime or "", year, manual,
        )
        return Response(data)


class RentaAssumptionsView(ManagedOrganizationAPIView):
    """Ajustes de la proyección: base mensual de ventas, gastos y planilla.
    Nulo = automático. No toca los actuals; solo cómo se proyectan los meses
    que faltan."""

    def put(self, request: Request) -> Response:
        from decimal import Decimal, InvalidOperation

        from .models import RentaProjection

        try:
            year = int(request.data.get("year") or timezone.localdate().year)
        except (TypeError, ValueError):
            return Response({"year": ["Año inválido."]}, status=status.HTTP_400_BAD_REQUEST)

        def _amount(key):
            raw = request.data.get(key, "")
            if raw in (None, ""):
                return None
            try:
                value = Decimal(str(raw))
            except InvalidOperation:
                raise ValueError(key)
            if value < 0:
                raise ValueError(key)
            return value

        try:
            fields = {
                "monthly_sales": _amount("monthly_sales"),
                "monthly_expenses": _amount("monthly_expenses"),
                "monthly_payroll": _amount("monthly_payroll"),
            }
        except ValueError as bad:
            return Response({str(bad): ["Monto inválido."]}, status=status.HTTP_400_BAD_REQUEST)

        RentaProjection.objects.update_or_create(
            account_ruc=request.ruc, year=year,
            defaults={**fields, "note": (request.data.get("note") or "")[:300], "updated_by": request.user},
        )
        data = renta_service.renta_summary(request.ruc, request.organization.tax_regime or "", year, load_manual_entries(request.ruc))
        return Response(data)


class PeriodCloseView(ManagedOrganizationAPIView):
    """Cierra (POST) o reabre (DELETE) un mes financiero. Cerrado = hecho
    firme, no se re-proyecta."""

    def post(self, request: Request) -> Response:
        from .models import FinancePeriodClose

        period = (request.data.get("period") or "").strip()
        if not (len(period) == 6 and period.isdigit()):
            return Response({"period": ["Indica el periodo como aaaamm."]}, status=status.HTTP_400_BAD_REQUEST)
        FinancePeriodClose.objects.get_or_create(
            account_ruc=request.ruc, period=period, defaults={"closed_by": request.user},
        )
        return Response({"period": period, "closed": True}, status=status.HTTP_201_CREATED)

    def delete(self, request: Request) -> Response:
        from .models import FinancePeriodClose

        period = (request.query_params.get("period") or "").strip()
        FinancePeriodClose.objects.filter(account_ruc=request.ruc, period=period).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
