"""Reconciliation API. Tenant-scoped; the engine itself never touches HTTP."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.tenancy import ManagedOrganizationAPIView, OrganizationAPIView
from finance_analytics.models import FinanceAlert

from .engine import ai as ai_engine
from .engine import statements as statements_engine
from .engine.run import run_reconciliation
from .models import (
    BankMovement, BankStatement, ConsistencyScore, DocumentReconciliation, InvoiceSettlement,
    ReconciliationRun, score_band,
)
from .models import BankStatement, StatementStatus
from .serializers import (
    BankMovementSerializer, BankStatementSerializer, DocumentReconciliationSerializer,
    MovementClassifySerializer,
)

logger = logging.getLogger(__name__)


def _period(request: Request) -> str | None:
    period = (request.query_params.get("period") or "").strip()
    if period and not (len(period) == 6 and period.isdigit()):
        return None
    return period or None


class SummaryView(OrganizationAPIView):
    """Everything the dashboard needs for one period, in one read."""

    def get(self, request: Request) -> Response:
        period = _period(request)
        runs = ReconciliationRun.objects.filter(account_ruc=request.ruc)
        run = (runs.filter(period=period) if period else runs).filter(status="done").first()
        if run is None:
            return Response({"run": None, "periods": sorted(
                set(runs.values_list("period", flat=True)), reverse=True)})
        score = ConsistencyScore.objects.filter(account_ruc=request.ruc, period=run.period).first()
        alerts = FinanceAlert.objects.filter(
            account_ruc=request.ruc, period=run.period, dedup_key__startswith="recon:",
        )
        settlements = InvoiceSettlement.objects.filter(account_ruc=request.ruc, billing_period=run.period)
        unpaid = settlements.filter(status__in=["unpaid", "partial"])
        return Response({
            "run": {
                "id": str(run.id), "period": run.period, "finished_at": run.finished_at,
                "totals": run.totals, "findings_count": run.findings_count,
            },
            "score": {"value": score.score, "band": score_band(score.score), "breakdown": score.breakdown} if score else None,
            "alerts": [
                {"id": str(a.id), "title": a.title, "explanation": a.explanation,
                 "severity": a.severity, "status": a.status,
                 "amount": float(a.amount) if a.amount is not None else None}
                for a in alerts
            ],
            "collections": {
                "invoices": settlements.count(),
                "pending_invoices": unpaid.count(),
                "pending_amount": float(sum((s.balance for s in unpaid), 0)),
            },
            "ai_explanation": run.ai_explanation or None,
            "periods": sorted(set(runs.values_list("period", flat=True)), reverse=True),
        })


class CollectionsView(OrganizationAPIView):
    """Cobranzas agrupadas por cliente, cruzando todos los periodos conciliados.

    La conciliación liquida cada factura emitida contra los abonos del banco;
    aquí se responde la pregunta comercial: ¿quién me debe, cuánto y desde
    cuándo? Cubre SOLO los periodos con conciliación corrida (``periods``):
    el motor liquida todas las facturas históricas en cada corrida, así que
    sin este recorte una factura de un mes jamás conciliado saldría como
    «deuda» cuando en realidad no hay extracto que pruebe nada.
    """

    PENDIENTES = ("unpaid", "partial")
    DETALLE_MAX = 10

    def get(self, request: Request) -> Response:
        periodos = set(
            ReconciliationRun.objects.filter(account_ruc=request.ruc, status="done")
            .values_list("period", flat=True)
        )
        settlements = (
            InvoiceSettlement.objects
            .filter(account_ruc=request.ruc, billing_period__in=periodos)
            .select_related("invoice").defer("invoice__xml_content", "invoice__raw")
            .order_by("invoice__issue_date", "invoice__period")
        )
        hoy = timezone.localdate()
        clientes: dict[str, dict] = {}
        for st in settlements:
            inv = st.invoice
            moneda = inv.currency or "PEN"
            clave = inv.receiver_ruc or (inv.receiver_name or "").strip().upper() or "sin-identificar"
            c = clientes.setdefault(clave, {
                "ruc": inv.receiver_ruc or "", "name": "Sin identificar",
                "invoices": 0, "pending_invoices": 0, "by_currency": {},
                "last_payment_date": None, "oldest_unpaid": None, "pending_detail": [],
            })
            if inv.receiver_name:
                c["name"] = inv.receiver_name
            bucket = c["by_currency"].setdefault(
                moneda,
                {"invoiced": Decimal("0"), "paid": Decimal("0"),
                 "credited": Decimal("0"), "pending": Decimal("0")},
            )
            bucket["invoiced"] += st.invoice_total
            bucket["paid"] += st.paid_amount
            bucket["credited"] += st.credit_notes_amount
            c["invoices"] += 1
            if st.last_payment_date and (
                c["last_payment_date"] is None or st.last_payment_date > c["last_payment_date"]
            ):
                c["last_payment_date"] = st.last_payment_date
            if st.status in self.PENDIENTES:
                c["pending_invoices"] += 1
                bucket["pending"] += st.balance
                pendiente = {
                    "id": str(inv.pk),
                    "full_number": inv.full_number, "issue_date": inv.issue_date,
                    "days": (hoy - inv.issue_date).days if inv.issue_date else None,
                    "balance": st.balance, "currency": moneda, "status": st.status,
                }
                # El queryset viene por fecha de emisión ascendente, así que la
                # primera pendiente con fecha es la más antigua del cliente.
                if c["oldest_unpaid"] is None or (
                    c["oldest_unpaid"]["issue_date"] is None and inv.issue_date
                ):
                    c["oldest_unpaid"] = pendiente
                if len(c["pending_detail"]) < self.DETALLE_MAX:
                    c["pending_detail"].append(pendiente)

        def pen(c, campo):
            return c["by_currency"].get("PEN", {}).get(campo, Decimal("0"))

        filas = sorted(
            clientes.values(),
            key=lambda c: (pen(c, "pending"), pen(c, "invoiced")), reverse=True,
        )
        total_moneda: dict[str, dict[str, Decimal]] = {}
        for c in filas:
            for moneda, bucket in c["by_currency"].items():
                acumulado = total_moneda.setdefault(
                    moneda,
                    {"invoiced": Decimal("0"), "paid": Decimal("0"),
                     "credited": Decimal("0"), "pending": Decimal("0")},
                )
                for campo, valor in bucket.items():
                    acumulado[campo] += valor
        return Response({
            "periods": sorted(periodos, reverse=True),
            "summary": {
                "invoices": sum(c["invoices"] for c in filas),
                "pending_invoices": sum(c["pending_invoices"] for c in filas),
                "customers_with_debt": sum(1 for c in filas if c["pending_invoices"]),
                "by_currency": total_moneda,
            } if filas else None,
            "customers": filas,
        })


class CustomerDocumentsView(OrganizationAPIView):
    """Las facturas de UN cliente, cada una con sus NC y su estado de cobranza.

    La ficha del cliente listaba los CPE crudos: facturas y NC mezcladas como
    filas hermanas, sin decir qué NC anula a qué factura ni si algo se cobró.
    Aquí la factura es la unidad y todo lo demás cuelga de ella. Se devuelve
    completo (tope MAX_DOCS) en vez de paginar: el filtro del panel es
    instantáneo y ningún cliente real se acerca al tope.
    """

    MAX_DOCS = 500

    def get(self, request: Request) -> Response:
        from sunat_cpe.models import Direction, DocumentClass, ElectronicInvoice

        from .engine.matching import _norm_number

        ruc = (request.query_params.get("ruc") or "").strip()
        if not (len(ruc) == 11 and ruc.isdigit()):
            return Response({"ruc": ["Indica ?ruc= del cliente (11 dígitos)."]},
                            status=status.HTTP_400_BAD_REQUEST)
        conciliados = set(
            ReconciliationRun.objects.filter(account_ruc=request.ruc, status="done")
            .values_list("period", flat=True)
        )
        emitidos = (
            ElectronicInvoice.objects.for_account(request.ruc)
            .filter(direction=Direction.ISSUED, receiver_ruc=ruc)
            .defer("xml_content", "raw")
        )
        facturas = list(
            emitidos.filter(document_class=DocumentClass.INVOICE)
            .order_by("-issue_date", "-period")[: self.MAX_DOCS]
        )
        liquidaciones = {
            s.invoice_id: s
            for s in InvoiceSettlement.objects.filter(
                account_ruc=request.ruc, invoice__in=facturas,
            )
        }
        por_numero = {_norm_number(f.full_number): f.pk for f in facturas}
        notas: dict[object, list[dict]] = {}  # pk de la factura → sus NC
        sueltas: list[dict] = []
        for nc in emitidos.filter(document_class=DocumentClass.CREDIT_NOTE,
                                  is_cancelled=False, is_rejected=False):
            fila = {"id": str(nc.pk), "full_number": nc.full_number, "issue_date": nc.issue_date,
                    "currency": nc.currency or "PEN", "amount": nc.total_amount or Decimal("0")}
            destino = por_numero.get(_norm_number(nc.references_document))
            if destino is None:
                sueltas.append({**fila, "references_document": nc.references_document})
            else:
                notas.setdefault(destino, []).append(fila)

        documentos = []
        for f in facturas:
            st = liquidaciones.get(f.pk)
            cobranza = None
            # Sin corrida sobre su periodo no se afirma nada: «sin pago» solo
            # vale cuando de verdad se miró el banco de ese mes.
            if st is not None and f.period in conciliados:
                cobranza = {
                    "status": st.status, "paid_amount": st.paid_amount,
                    "balance": st.balance, "last_payment_date": st.last_payment_date,
                }
            nc_de_f = notas.get(f.pk, [])
            documentos.append({
                "id": str(f.pk), "full_number": f.full_number,
                "issue_date": f.issue_date, "period": f.period,
                "currency": f.currency or "PEN",
                "total_amount": f.total_amount or Decimal("0"),
                "is_cancelled": f.is_cancelled, "is_rejected": f.is_rejected,
                "credit_notes": nc_de_f,
                "credit_notes_amount": sum((n["amount"] for n in nc_de_f), Decimal("0")),
                "settlement": cobranza,
            })
        return Response({
            "count": len(documentos),
            "truncated": len(facturas) == self.MAX_DOCS,
            "reconciled_periods": len(conciliados),
            "documents": documentos,
            "unassigned_credit_notes": sueltas,
        })


class RunView(ManagedOrganizationAPIView):
    def post(self, request: Request) -> Response:
        period = (request.data.get("period") or "").strip()
        if not (len(period) == 6 and period.isdigit()):
            return Response({"period": ["Indica el periodo como aaaamm."]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            run = run_reconciliation(request.ruc, period)
        except Exception:  # noqa: BLE001 — quedó registrado en el run
            logger.exception("Reconciliation failed for %s %s", request.ruc, period)
            return Response({"detail": "La conciliación falló; revisa el detalle en el historial."},
                            status=status.HTTP_502_BAD_GATEWAY)
        return Response({"id": str(run.id), "period": run.period, "totals": run.totals,
                         "findings_count": run.findings_count}, status=status.HTTP_201_CREATED)


class DocumentsView(OrganizationAPIView):
    def get(self, request: Request) -> Response:
        period = _period(request)
        if not period:
            return Response({"detail": "Indica ?period=aaaamm."}, status=status.HTTP_400_BAD_REQUEST)
        qs = DocumentReconciliation.objects.filter(account_ruc=request.ruc, period=period)
        direction = request.query_params.get("direction")
        if direction in ("sales", "purchases"):
            qs = qs.filter(direction=direction)
        if request.query_params.get("only") == "issues":
            qs = qs.exclude(level="ok")
        return Response(DocumentReconciliationSerializer(qs[:500], many=True).data)


class MovementsView(OrganizationAPIView):
    def get(self, request: Request) -> Response:
        qs = BankMovement.objects.filter(account_ruc=request.ruc)
        period = _period(request)
        if period:
            qs = qs.filter(period=period)
        return Response(BankMovementSerializer(qs[:500], many=True).data)

    def post(self, request: Request) -> Response:
        many = isinstance(request.data, list)
        serializer = BankMovementSerializer(data=request.data, many=many)
        serializer.is_valid(raise_exception=True)
        serializer.save(account_ruc=request.ruc, source="manual" if not many else "import")
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class MovementClassifyView(ManagedOrganizationAPIView):
    def post(self, request: Request, pk) -> Response:
        movement = BankMovement.objects.filter(account_ruc=request.ruc, pk=pk).first()
        if movement is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = MovementClassifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        movement.category = serializer.validated_data["category"]
        movement.classified_by = BankMovement.ClassifiedBy.USER
        movement.classified_by_user = request.user
        movement.classified_at = timezone.now()
        movement.confidence = 1.0
        movement.evidence = ["Clasificado por el usuario"]
        movement.save(update_fields=[
            "category", "classified_by", "classified_by_user", "classified_at",
            "confidence", "evidence", "updated_at",
        ])
        return Response(BankMovementSerializer(movement).data)


class ExplainView(ManagedOrganizationAPIView):
    """Generates (or returns) the AI explanation for the latest run of a period."""

    def post(self, request: Request) -> Response:
        period = (request.data.get("period") or "").strip()
        run = ReconciliationRun.objects.filter(account_ruc=request.ruc, period=period, status="done").first()
        if run is None:
            return Response({"detail": "Primero corre la conciliación del periodo."}, status=status.HTTP_409_CONFLICT)
        if run.ai_explanation and not request.data.get("force"):
            return Response(run.ai_explanation)
        try:
            return Response(ai_engine.explain(run))
        except Exception:  # noqa: BLE001
            logger.exception("AI explanation failed for %s %s", request.ruc, period)
            return Response({"detail": "No se pudo generar la explicación."}, status=status.HTTP_502_BAD_GATEWAY)


class StatementsView(OrganizationAPIView):
    """Bank account statements: list what has been uploaded, and upload a new
    PDF (optionally with a one-off password). Each upload is parsed on the spot;
    its movements feed the reconciliation engine."""

    parser_classes = [MultiPartParser, FormParser]

    def get(self, request: Request) -> Response:
        qs = BankStatement.objects.filter(account_ruc=request.ruc)
        return Response({
            "password_hint": "Los 8 dígitos del RUC después del 2.º y sin el último.",
            "default_password_preview": statements_engine.statement_password(request.ruc),
            "statements": BankStatementSerializer(qs[:100], many=True).data,
        })

    def post(self, request: Request) -> Response:
        if not request.membership.can_manage:
            return Response({"detail": "Solo el titular o el contador pueden cargar estados de cuenta."},
                            status=status.HTTP_403_FORBIDDEN)
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"file": ["Adjunta el PDF del estado de cuenta."]}, status=status.HTTP_400_BAD_REQUEST)
        if not upload.name.lower().endswith(".pdf"):
            return Response({"file": ["El estado de cuenta debe ser un PDF."]}, status=status.HTTP_400_BAD_REQUEST)
        currency = (request.data.get("currency") or "PEN").upper()[:3]
        statement = BankStatement.objects.create(
            account_ruc=request.ruc, file=upload, original_name=upload.name[:255],
            bank=(request.data.get("bank") or "")[:60], bank_account=(request.data.get("bank_account") or "")[:40],
            currency=currency, uploaded_by=request.user,
        )
        statement._password_override = request.data.get("password") or ""
        try:
            result = statements_engine.import_statement(statement)
        except statements_engine.WrongPassword:
            statement.status = StatementStatus.LOCKED
            statement.error = "No pudimos abrir el PDF: la contraseña no coincide."
            statement.save(update_fields=["status", "error", "updated_at"])
            return Response({
                "id": str(statement.id), "status": statement.status,
                "detail": "El PDF está protegido y la contraseña no abrió. Indica la contraseña del banco.",
            }, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Statement parse failed for %s", request.ruc)
            statement.status = StatementStatus.FAILED
            statement.error = str(exc)[:500]
            statement.save(update_fields=["status", "error", "updated_at"])
            return Response({"detail": f"No pudimos leer el estado de cuenta: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({
            "statement": BankStatementSerializer(statement).data,
            "imported": result["created"], "detected": result["parsed"],
        }, status=status.HTTP_201_CREATED)


class StatementDeleteView(ManagedOrganizationAPIView):
    def delete(self, request: Request, pk) -> Response:
        statement = BankStatement.objects.filter(account_ruc=request.ruc, pk=pk).first()
        if statement is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        # Its movements go too, unless a person already reclassified them by hand.
        statement.movements.exclude(classified_by=BankMovement.ClassifiedBy.USER).delete()
        if statement.file:
            statement.file.delete(save=False)
        statement.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
