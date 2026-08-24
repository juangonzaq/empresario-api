"""API for the fee receipts (recibos por honorarios) received.

The front's Honorarios view lists them per period with the three totals
the accountant reads first: gross expense, 8 % withheld, net paid.

Registration is MANUAL by design, not a fallback: SUNAT's SOL menu only
offers RHE consultations to the ISSUER — the paying company gets no
listing (verified against the full menu tree of the connected SOL user).
So the receipt the worker hands over is typed here, lands in the income
statement automatically, and if SUNAT ever exposes a listing the scraper
will merge on the same unique key without duplicating.
"""

from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.tenancy import ManagedOrganizationAPIView, OrganizationAPIView

from .models import FeeReceipt


class FeeReceiptsView(OrganizationAPIView):
    """``GET /api/rhe/receipts/?period=aaaamm`` — one period's receipts,
    newest first, with totals and the list of periods that have data."""

    def get(self, request: Request) -> Response:
        base = FeeReceipt.objects.for_account(request.ruc)
        periods = sorted(
            {p for p in base.values_list("period", flat=True) if p},
            reverse=True,
        )
        period = (request.query_params.get("period") or "").strip()
        if not period and periods:
            period = periods[0]
        receipts = base.for_period(period) if period else base.none()

        totals = {"gross": Decimal("0"), "withheld": Decimal("0"),
                  "net": Decimal("0")}
        rows = []
        for receipt in receipts:
            if not receipt.is_reverted:
                totals["gross"] += receipt.gross_amount or 0
                totals["withheld"] += receipt.income_tax_withheld or 0
                totals["net"] += receipt.net_amount or 0
            rows.append({
                "id": str(receipt.pk),
                "issuer_doc": receipt.issuer_doc,
                "issuer_name": receipt.issuer_name,
                "full_number": receipt.full_number,
                "issue_date": receipt.issue_date,
                "currency": receipt.currency,
                "gross_amount": receipt.gross_amount,
                "income_tax_withheld": receipt.income_tax_withheld,
                "net_amount": receipt.net_amount,
                "status": receipt.status,
                "is_reverted": receipt.is_reverted,
                "is_manual": (receipt.raw or {}).get("origin") == "manual",
                "detail": receipt.detail or {},
                "detail_fetched_at": receipt.detail_fetched_at,
            })
        return Response({
            "period": period,
            "periods": periods,
            "totals": totals,
            "receipts": rows,
        })

    def get_permissions(self):
        if self.request.method == "POST":
            return [
                permission() for permission in
                ManagedOrganizationAPIView.permission_classes
            ]
        return super().get_permissions()

    def post(self, request: Request) -> Response:
        """Register one receipt from the paper/PDF the worker handed over."""
        receipt, error, code = _create_receipt(request.ruc, request.data)
        if error:
            return Response({"detail": error}, status=code)
        return Response({"id": str(receipt.pk)}, status=status.HTTP_201_CREATED)


def _create_receipt(account_ruc: str, data) -> tuple:
    """(receipt, None, 201) or (None, detail, http_status)."""
    bad = status.HTTP_400_BAD_REQUEST
    full = str(data.get("full_number") or "").strip().upper()
    series, _, number = full.partition("-")
    # Same normalization as the scraper, so both paths merge on one key.
    series, number = series.strip(), number.strip().lstrip("0")
    if not series or not number:
        return None, "Indica el número como serie-número, ej. E001-8.", bad
    issuer_doc = str(data.get("issuer_doc") or "").strip()
    if not issuer_doc.isdigit() or len(issuer_doc) not in (8, 11):
        return None, "El documento del emisor debe ser RUC (11) o DNI (8).", bad
    issuer_name = str(data.get("issuer_name") or "").strip()
    if not issuer_name:
        return None, "Indica el nombre del emisor.", bad
    try:
        issue_date = datetime.date.fromisoformat(str(data.get("issue_date")))
    except (TypeError, ValueError):
        return None, "Indica la fecha como aaaa-mm-dd.", bad
    try:
        gross = Decimal(str(data.get("gross_amount")))
        withheld = Decimal(str(data.get("income_tax_withheld") or "0"))
    except (InvalidOperation, TypeError, ValueError):
        return None, "Importe no válido.", bad
    if gross <= 0 or withheld < 0 or withheld > gross:
        return None, ("El bruto debe ser mayor a cero y la retención no "
                      "puede superarlo."), bad
    if FeeReceipt.objects.filter(
        account_ruc=account_ruc, issuer_doc=issuer_doc,
        series=series, number=number,
    ).exists():
        return None, (f"El recibo {series}-{number} de ese emisor ya "
                      "está registrado."), status.HTTP_409_CONFLICT

    receipt = FeeReceipt.objects.create(
        account_ruc=account_ruc,
        issuer_doc=issuer_doc,
        issuer_doc_type="6" if len(issuer_doc) == 11 else "1",
        issuer_name=issuer_name[:200],
        series=series,
        number=number,
        full_number=f"{series}-{number}",
        issue_date=issue_date,
        period=f"{issue_date.year}{issue_date.month:02d}",
        currency="PEN",
        gross_amount=gross,
        income_tax_withheld=withheld,
        net_amount=gross - withheld,
        status="Registrado",
        raw={"origin": "manual"},
    )
    _ingest(account_ruc)
    return receipt, None, status.HTTP_201_CREATED


def _ingest(taxpayer_id: str) -> None:
    """Straight to the income statement, same as the scraped ones."""
    from financials.services import ingest

    ingest.ingest_fee_receipts(taxpayer_id)


class FeeReceiptUploadView(ManagedOrganizationAPIView):
    """``POST`` the receipt's PDF: SUNAT generates it with a stable
    layout, so the fields are extracted and the receipt registers itself.
    When the PDF is a scan or a field is missing, the parsed values come
    back with 422 and the form opens pre-filled — never a dead end."""

    def post(self, request: Request) -> Response:
        upload = request.FILES.get("file")
        if upload is None:
            return Response(
                {"detail": "Adjunta el PDF del recibo en «file»."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if upload.size > 5 * 1024 * 1024:
            return Response(
                {"detail": "El PDF no debe superar 5 MB."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from .services.pdf_extract import PdfExtractError, extract_fee_receipt

        try:
            parsed = extract_fee_receipt(upload.read(), request.ruc)
        except PdfExtractError as error:
            return Response(
                {"detail": str(error), "parsed": {}},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        required = ("full_number", "issuer_doc", "issuer_name",
                    "issue_date", "gross_amount")
        if all(parsed.get(field) for field in required):
            receipt, error, code = _create_receipt(request.ruc, parsed)
            if receipt is not None:
                return Response(
                    {"id": str(receipt.pk), "parsed": parsed}, status=code
                )
            return Response({"detail": error, "parsed": parsed}, status=code)
        return Response(
            {
                "detail": "Leí el PDF pero faltan datos: revisa y completa "
                          "el formulario.",
                "parsed": parsed,
            },
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )


class FeeReceiptRefreshView(ManagedOrganizationAPIView):
    """``POST`` re-scrapes ONE receipt's period from SUNAT (list plus
    detail) in the background; the row updates when the worker finishes."""

    def post(self, request: Request, pk) -> Response:
        receipt = get_object_or_404(FeeReceipt, pk=pk, account_ruc=request.ruc)
        if not receipt.period:
            return Response(
                {"detail": "Este recibo no tiene periodo: no hay qué "
                           "consultar."},
                status=status.HTTP_409_CONFLICT,
            )
        from .tasks import refresh_receipt

        refresh_receipt.apply_async(
            args=[str(receipt.pk)], queue="scraping"
        )
        return Response(
            {"detail": "Actualizando desde SUNAT…"},
            status=status.HTTP_202_ACCEPTED,
        )


class FeeReceiptView(ManagedOrganizationAPIView):
    """``DELETE`` a manually registered receipt (a scraped one would come
    back on the next sync, so those are corrected at the source)."""

    def delete(self, request: Request, pk) -> Response:
        receipt = get_object_or_404(
            FeeReceipt, pk=pk, account_ruc=request.ruc
        )
        if (receipt.raw or {}).get("origin") != "manual":
            return Response(
                {"detail": "Este recibo vino de una sincronización: no se "
                           "elimina a mano."},
                status=status.HTTP_409_CONFLICT,
            )
        receipt_pk = str(receipt.pk)
        receipt.delete()
        from financials.models import FinancialTransaction

        FinancialTransaction.objects.filter(
            taxpayer_id=request.ruc, source="fee_receipt",
            external_id=receipt_pk,
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
