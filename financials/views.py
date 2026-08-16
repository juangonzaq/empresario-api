"""Financial dashboard API (spec §11). Every figure is traceable: the
drilldown endpoint sustains the promise that any visible number opens the
transactions that compose it (§0.7)."""

from __future__ import annotations

import datetime

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.tenancy import ManagedOrganizationAPIView, OrganizationAPIView

from .models import (
    CategorizationStatus, FinancialTransaction, SettlementStatus,
    TransactionCategory,
)
from .services import categorization, ingest, ratios, statements


def _year(request: Request) -> int:
    try:
        return int(request.query_params.get("year") or datetime.date.today().year)
    except ValueError:
        return datetime.date.today().year


def _category_or_400(taxpayer_id: str, code: str) -> TransactionCategory | None:
    return (
        TransactionCategory.objects.filter(
            taxpayer_id__in=["", taxpayer_id], code=code, is_active=True
        )
        .order_by("-taxpayer_id")
        .first()
    )


def _transaction_payload(transaction: FinancialTransaction) -> dict:
    return {
        "id": str(transaction.id),
        "source": transaction.source,
        "external_id": transaction.external_id,
        "direction": transaction.direction,
        "document_kind": transaction.document_kind,
        "description": transaction.description,
        "issue_date": transaction.issue_date,
        "accounting_date": transaction.accounting_date,
        "counterparty_tax_id": transaction.counterparty_tax_id,
        "counterparty_name": transaction.counterparty_name,
        "currency": transaction.currency,
        "net_amount_pen": transaction.net_amount_pen,
        "total_amount_pen": transaction.total_amount_pen,
        "category_code": transaction.category.code if transaction.category else None,
        "category_name": transaction.category.name if transaction.category else None,
        "categorization_status": transaction.categorization_status,
        "suggestion_reason": transaction.suggestion_reason,
        "is_credit_note": transaction.is_credit_note,
        "settlement_status": transaction.settlement_status,
    }


class SyncView(ManagedOrganizationAPIView):
    """POST — ingest every source and run the categorization cascade."""

    def post(self, request: Request) -> Response:
        ingested = ingest.ingest_all(request.ruc)
        categorized = categorization.categorize_pending(request.ruc)
        return Response({"ingested": ingested, "categorized": categorized})


class IncomeStatementView(OrganizationAPIView):
    def get(self, request: Request) -> Response:
        year = _year(request)
        return Response({
            **statements.income_statement(request.ruc, year),
            "coverage": categorization.coverage(request.ruc, year),
        })


class BalanceSheetView(OrganizationAPIView):
    def get(self, request: Request) -> Response:
        year = _year(request)
        month = int(request.query_params.get("month") or 12)
        return Response(statements.balance_sheet(request.ruc, year, month))


class RatiosView(OrganizationAPIView):
    def get(self, request: Request) -> Response:
        year = _year(request)
        return Response({
            "year": year,
            "ratios": ratios.annual_ratios(request.ruc, year),
        })


class MonthlyRatiosView(OrganizationAPIView):
    def get(self, request: Request) -> Response:
        year = _year(request)
        return Response({
            "year": year,
            "months": ratios.monthly_management_ratios(request.ruc, year),
        })


class KpisView(OrganizationAPIView):
    def get(self, request: Request) -> Response:
        return Response(ratios.kpis(request.ruc, _year(request)))


class TransactionsView(OrganizationAPIView):
    """Paginated listing with the filters the categorization screen needs."""

    PAGE_SIZE = 50

    def get(self, request: Request) -> Response:
        rows = FinancialTransaction.objects.filter(
            taxpayer_id=request.ruc
        ).select_related("category")
        params = request.query_params
        if params.get("status"):
            rows = rows.filter(categorization_status=params["status"])
        if params.get("year"):
            rows = rows.filter(accounting_date__year=params["year"])
        if params.get("month"):
            rows = rows.filter(accounting_date__month=params["month"])
        if params.get("category"):
            rows = rows.filter(category__code=params["category"])
        if params.get("counterparty"):
            rows = rows.filter(counterparty_tax_id=params["counterparty"])
        if params.get("direction"):
            rows = rows.filter(direction=params["direction"])
        if params.get("search"):
            from django.db.models import Q

            term = params["search"]
            rows = rows.filter(
                Q(counterparty_name__icontains=term)
                | Q(counterparty_tax_id__icontains=term)
                | Q(description__icontains=term)
                | Q(external_id__icontains=term)
            )
        try:
            page = max(int(params.get("page") or 1), 1)
        except ValueError:
            page = 1
        total = rows.count()
        start = (page - 1) * self.PAGE_SIZE
        return Response({
            "count": total,
            "page": page,
            "page_size": self.PAGE_SIZE,
            "pages": max((total + self.PAGE_SIZE - 1) // self.PAGE_SIZE, 1),
            "results": [
                _transaction_payload(t) for t in rows[start:start + self.PAGE_SIZE]
            ],
        })


class PendingTransactionsView(OrganizationAPIView):
    """§13.3 — the pending queue grouped by counterparty, biggest amounts
    first, each group carrying its suggestion and the reason."""

    def get(self, request: Request) -> Response:
        pending = FinancialTransaction.objects.filter(
            taxpayer_id=request.ruc,
            categorization_status__in=[
                CategorizationStatus.UNCATEGORIZED,
                CategorizationStatus.SUGGESTED,
            ],
        ).select_related("category")

        groups: dict[str, dict] = {}
        for transaction in pending:
            key = transaction.counterparty_tax_id or transaction.counterparty_name or "—"
            group = groups.setdefault(key, {
                "counterparty_tax_id": transaction.counterparty_tax_id,
                "counterparty_name": transaction.counterparty_name or "Sin identificar",
                "count": 0,
                "total_amount_pen": 0.0,
                "suggested_category_code": None,
                "suggested_category_name": None,
                "suggestion_reason": "",
                "transactions": [],
            })
            group["count"] += 1
            group["total_amount_pen"] = round(
                group["total_amount_pen"] + float(transaction.total_amount_pen), 2
            )
            if transaction.category and group["suggested_category_code"] is None:
                group["suggested_category_code"] = transaction.category.code
                group["suggested_category_name"] = transaction.category.name
                group["suggestion_reason"] = transaction.suggestion_reason
            group["transactions"].append(_transaction_payload(transaction))

        ordered = sorted(
            groups.values(), key=lambda g: g["total_amount_pen"], reverse=True
        )
        return Response({
            "groups": ordered,
            "coverage": categorization.coverage(
                request.ruc, datetime.date.today().year
            ),
        })


class BulkCategorizeView(ManagedOrganizationAPIView):
    """POST {ids, category_code, create_rule} — a month of invoices from a
    recurrent supplier resolves in one click, not forty (§6.3)."""

    def post(self, request: Request) -> Response:
        ids = request.data.get("ids")
        code = str(request.data.get("category_code") or "").strip()
        if not isinstance(ids, list) or not ids:
            return Response(
                {"detail": "Manda los ids de las transacciones."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        category = _category_or_400(request.ruc, code)
        if category is None:
            return Response(
                {"detail": f"Categoría desconocida: {code}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rows = FinancialTransaction.objects.filter(
            taxpayer_id=request.ruc, id__in=ids
        )
        confirmed = 0
        first: FinancialTransaction | None = None
        for transaction in rows:
            categorization.confirm(transaction, category, request.user)
            first = first or transaction
            confirmed += 1

        rule_created = False
        if request.data.get("create_rule") is True and first is not None \
                and first.counterparty_tax_id:
            categorization.create_rule_from(first, category)
            rule_created = True
            # The new rule resolves the rest of this counterparty's queue.
            categorization.categorize_pending(request.ruc)
        return Response({"confirmed": confirmed, "rule_created": rule_created})


class ExcludeTransactionView(ManagedOrganizationAPIView):
    """POST — take a row out of the statements (duplicates, personal
    expenses). Explicit and reversible, never a delete."""

    def post(self, request: Request, pk) -> Response:
        transaction = get_object_or_404(
            FinancialTransaction, pk=pk, taxpayer_id=request.ruc
        )
        transaction.categorization_status = CategorizationStatus.EXCLUDED
        transaction.save(update_fields=["categorization_status", "updated_at"])
        return Response(_transaction_payload(transaction))


class SettleTransactionView(ManagedOrganizationAPIView):
    """POST — mark an invoice as collected or paid. Receivables and
    payables derive from this, so it feeds the balance directly."""

    def post(self, request: Request, pk) -> Response:
        transaction = get_object_or_404(
            FinancialTransaction, pk=pk, taxpayer_id=request.ruc
        )
        transaction.settlement_status = SettlementStatus.SETTLED
        transaction.settled_amount = transaction.total_amount_pen
        transaction.save(
            update_fields=["settlement_status", "settled_amount", "updated_at"]
        )
        return Response(_transaction_payload(transaction))


class DrilldownView(OrganizationAPIView):
    """§11 — the transactions behind one statement line and month. This
    is what makes every number on the board clickable to its documents."""

    def get(self, request: Request) -> Response:
        line_code = (request.query_params.get("line") or "").strip()
        year = _year(request)
        month = request.query_params.get("month")
        if not line_code:
            return Response(
                {"detail": "Indica la línea del estado."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        categories = TransactionCategory.objects.filter(
            taxpayer_id__in=["", request.ruc],
            statement_line__code=line_code,
        )
        rows = FinancialTransaction.objects.filter(
            taxpayer_id=request.ruc,
            category__in=categories,
            categorization_status=CategorizationStatus.CONFIRMED,
            accounting_date__year=year,
        ).select_related("category")
        if month:
            rows = rows.filter(accounting_date__month=month)
        return Response({
            "line": line_code,
            "year": year,
            "month": month,
            "transactions": [_transaction_payload(t) for t in rows[:200]],
        })


class CategoriesView(OrganizationAPIView):
    """The category catalog the categorization screen offers."""

    def get(self, request: Request) -> Response:
        rows = TransactionCategory.objects.filter(
            taxpayer_id__in=["", request.ruc], is_active=True
        ).order_by("display_order")
        seen: dict[str, TransactionCategory] = {}
        for category in rows:
            if category.code not in seen or category.taxpayer_id:
                seen[category.code] = category
        return Response([
            {
                "code": c.code,
                "name": c.name,
                "statement": c.statement,
                "sign": c.sign,
                "applies_to": c.applies_to,
            }
            for c in seen.values()
        ])
