"""API for the executive finance analytics (CPE + ITF).

Read endpoints aggregate over the FULL dataset (never one page), and every
amount is grouped by currency. The raw CPE/ITF APIs stay available for audit.
"""

from __future__ import annotations

import logging

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from sunat_cpe.models import Direction, ElectronicInvoice

from accounts.tenancy import ManagedOrganizationAPIView, OrganizationAPIView

from . import cache as overview_cache

from .models import SEVERITY_RANK, ActionStatus, AlertStatus, FinanceAlert
from .services import ai_summary as ai_service
from .services import consistency as consistency_service
from .services import itf_summary as itf_service
from .services import parties as parties_service
from .services.common import clean_name, money
from .services.cpe_summary import (
    credit_notes_detail, load_documents, period_documents_for,
    purchases_summary, sales_summary,
)

logger = logging.getLogger(__name__)


class SalesView(OrganizationAPIView):
    def get(self, request: Request) -> Response:
        return Response(sales_summary(load_documents(request.ruc)))


class PurchasesView(OrganizationAPIView):
    def get(self, request: Request) -> Response:
        return Response(purchases_summary(load_documents(request.ruc)))


class CustomersView(OrganizationAPIView):
    def get(self, request: Request) -> Response:
        return Response(parties_service.customers_analysis(load_documents(request.ruc)))


class SuppliersView(OrganizationAPIView):
    def get(self, request: Request) -> Response:
        return Response(parties_service.suppliers_analysis(load_documents(request.ruc)))


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
    def get(self, request: Request) -> Response:
        return Response(itf_service.itf_summary(request.ruc))


class ConsistencyView(OrganizationAPIView):
    def get(self, request: Request) -> Response:
        return Response(consistency_service.consistency_analysis(load_documents(request.ruc), request.ruc))


class OverviewView(OrganizationAPIView):
    """Una sola llamada para los cards del Home y la pestaña Resumen.

    Se cachea por empresa. Antes esta vista además *reconstruía las alertas*
    en cada GET: 19 escrituras por cada carga de pantalla, con la base
    aguantando la contención y el endpoint imposible de cachear. Las alertas
    se recalculan donde corresponde —al terminar una sincronización, que es
    cuando los datos cambian— y aquí solo se leen.
    """

    def get(self, request: Request) -> Response:
        cached = overview_cache.get_overview(request.ruc)
        if cached is not None:
            return Response(cached)
        return Response(self._build(request))

    def _build(self, request: Request) -> dict:
        docs = load_documents(request.ruc)
        sales = sales_summary(docs, months=13)
        purchases = purchases_summary(docs, months=13)
        customers = parties_service.customers_analysis(docs)
        itf = itf_service.itf_summary(request.ruc)
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
            "customers": customers["summary"],
            "top_customers": customers["parties"][:5],
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
            "ai_summary": cached_ai,
            # De dónde salen los números del briefing; se calculan aquí, nunca
            # los redacta el modelo.
            "sources": ai_service.briefing_sources(docs, itf, len(open_alerts)),
        }
        overview_cache.set_overview(request.ruc, payload)
        return payload


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


class InvoiceInsightView(OrganizationAPIView):
    """Normalized detail of one comprobante for the UI drawer.

    Never returns the full XML; the raw document stays on the audit API
    (``/api/cpe/invoices/{id}/``).
    """

    def get(self, request: Request, pk: str) -> Response:
        invoice = get_object_or_404(
            ElectronicInvoice.objects.select_related("extract"),
            pk=pk, account_ruc=request.ruc,
        )
        extract = getattr(invoice, "extract", None)
        return Response({
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
