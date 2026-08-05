"""Turn the Consultar Factura JSON records into ElectronicInvoice field dicts."""

from __future__ import annotations

import calendar
import datetime
import json
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from ..models import Direction
from .constants import COD_CPE_CLASS, TIPO_CONSULTA

logger = logging.getLogger(__name__)


def parse_records(html: str) -> list[dict[str, Any]]:
    """Extract the comprobante list from the ``<textarea>`` JSON envelope."""
    match = re.search(r"<textarea[^>]*>(.*?)</textarea>", html, re.S)
    if not match:
        return []
    try:
        envelope = json.loads(match.group(1))
    except (ValueError, TypeError):
        logger.warning("CPE response was not valid JSON")
        return []
    data = envelope.get("data") or "[]"
    try:
        records = json.loads(data) if isinstance(data, str) else data
    except (ValueError, TypeError):
        return []
    return records or []


def parse_amount(value: str | None) -> Decimal | None:
    if not value:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", value.replace(",", ""))
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_date(value: str | None) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def _name_from_desc(desc: str | None) -> str:
    if not desc:
        return ""
    parts = desc.split(" - ", 1)
    return parts[1].strip() if len(parts) == 2 else desc.strip()


def record_fields(
    record: dict[str, Any], account_ruc: str, tipo_consulta: str
) -> dict[str, Any]:
    """Map one SUNAT JSON record onto ElectronicInvoice fields.

    ``account_ruc`` is the logged-in account (fixed); the issuer may be a third
    party for received documents. Direction is derived from who issued it, which
    is unambiguous, rather than from the query type.
    """
    issue_date = parse_date(record.get("fechaEmisionDesc"))
    period = f"{issue_date.year}{issue_date.month:02d}" if issue_date else ""
    issuer_ruc = (record.get("nroRucEmisor") or "").strip()
    cod_cpe = (record.get("codCpe") or "").strip()

    default_class, _default_dir, rejected_view = TIPO_CONSULTA.get(
        tipo_consulta, (None, None, False)
    )
    document_class = COD_CPE_CLASS.get(cod_cpe, default_class)
    direction = Direction.ISSUED if issuer_ruc == account_ruc else Direction.RECEIVED
    is_rejected = rejected_view or bool((record.get("fechaRechazoDesc") or "").strip())

    return {
        "account_ruc": account_ruc,
        "direction": direction,
        "document_class": document_class,
        "document_type": (record.get("tipoCPE") or "").strip(),
        "cpe_code": cod_cpe,
        "download_code": (record.get("codFactura") or "").strip(),
        "tipo_consulta": tipo_consulta,
        "issuer_ruc": issuer_ruc,
        "issuer_name": _name_from_desc(record.get("nroRucEmisorDesc")),
        "receiver_doc_type": (record.get("codTipoDocReceptor") or "").strip(),
        "receiver_ruc": (record.get("nroRucReceptor") or "").strip(),
        "receiver_name": _name_from_desc(record.get("nroRucReceptorDesc")),
        "series": (record.get("nroSerie") or "").strip(),
        "number": (record.get("nroFactura") or "").strip(),
        "full_number": (record.get("nroFacturaDesc") or "").strip(),
        "issue_date": issue_date,
        "period": period,
        "currency": (record.get("codigoMoneda") or "").strip(),
        "currency_symbol": (record.get("codigoMonedaDesc") or "").strip(),
        "total_amount": parse_amount(record.get("importeTotalDesc")),
        "status": (record.get("estadoDesc") or "").strip(),
        "is_cancelled": str(record.get("ind_anulado") or "0") not in ("0", ""),
        "is_rejected": is_rejected,
        "reject_date": (record.get("fechaRechazoDesc") or "").strip(),
        "references_document": (record.get("comprobantePorElQueSeEmite") or "").strip(),
        "xml_id": (record.get("numeroIdXml") or "").strip(),
        "can_download": str(record.get("ind_puede_descargar") or "0") in ("1", "true"),
        "raw": record,
    }


def month_bounds(period: str) -> tuple[str, str]:
    """(fec_desde, fec_hasta) as dd/mm/yyyy for a yyyymm period."""
    year, month = int(period[:4]), int(period[4:])
    last = calendar.monthrange(year, month)[1]
    return f"01/{month:02d}/{year}", f"{last:02d}/{month:02d}/{year}"


def previous_period(period: str) -> str:
    year, month = int(period[:4]), int(period[4:]) - 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year}{month:02d}"


def current_period(today: datetime.date | None = None) -> str:
    today = today or datetime.date.today()
    return f"{today.year}{today.month:02d}"


def recent_periods(count: int, today: datetime.date | None = None) -> list[str]:
    """The current period plus ``count`` earlier ones, newest first."""
    period = current_period(today)
    periods = [period]
    for _ in range(count):
        period = previous_period(period)
        periods.append(period)
    return periods
