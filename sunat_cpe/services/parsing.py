"""Turn the Consultar Factura JSON records into ElectronicInvoice field dicts."""

from __future__ import annotations

import calendar
import datetime
import html as html_entities
import json
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from ..models import Direction
from .constants import COD_CPE_CLASS, TIPO_CONSULTA

logger = logging.getLogger(__name__)

# El importe llega como texto con su símbolo: "S/7,227.50" o, en dólares,
# "&#36;2,320.00" — SUNAT manda el "$" como entidad HTML. Se toma el primer
# número completo del texto en lugar de borrar lo que no sea dígito: así
# ningún prefijo puede pegarse al importe.
_AMOUNT = re.compile(r"\d[\d,]*(?:\.\d+)?")


def clean_field(value: Any) -> str:
    """Texto del registro ya sin entidades HTML y sin espacios sobrantes."""
    return html_entities.unescape(str(value or "")).strip()


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
    """'S/7,227.50' → 7227.50; '&#36;2,320.00' → 2320.00.

    Las entidades HTML se decodifican ANTES de leer el número: «&#36;» son
    los dígitos 3 y 6 hasta que se convierten en «$», y borrando solo los no
    dígitos quedaban pegados al importe (2,320.00 se volvía 362320.00).
    """
    if not value:
        return None
    text = html_entities.unescape(str(value))
    match = _AMOUNT.search(text)
    if not match:
        return None
    try:
        amount = Decimal(match.group().replace(",", ""))
    except InvalidOperation:
        return None
    # El signo puede venir antes del símbolo de moneda ("-S/1,000.00").
    return -amount if "-" in text[: match.start()] else amount


def parse_date(value: str | None) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def _name_from_desc(desc: str | None) -> str:
    cleaned = clean_field(desc)
    if not cleaned:
        return ""
    parts = cleaned.split(" - ", 1)
    return parts[1].strip() if len(parts) == 2 else cleaned


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
    issuer_ruc = clean_field(record.get("nroRucEmisor"))
    cod_cpe = clean_field(record.get("codCpe"))

    default_class, _default_dir, rejected_view = TIPO_CONSULTA.get(
        tipo_consulta, (None, None, False)
    )
    document_class = COD_CPE_CLASS.get(cod_cpe, default_class)
    direction = Direction.ISSUED if issuer_ruc == account_ruc else Direction.RECEIVED
    is_rejected = rejected_view or bool(clean_field(record.get("fechaRechazoDesc")))

    return {
        "account_ruc": account_ruc,
        "direction": direction,
        "document_class": document_class,
        "document_type": clean_field(record.get("tipoCPE")),
        "cpe_code": cod_cpe,
        "download_code": clean_field(record.get("codFactura")),
        "tipo_consulta": tipo_consulta,
        "issuer_ruc": issuer_ruc,
        "issuer_name": _name_from_desc(record.get("nroRucEmisorDesc")),
        "receiver_doc_type": clean_field(record.get("codTipoDocReceptor")),
        "receiver_ruc": clean_field(record.get("nroRucReceptor")),
        "receiver_name": _name_from_desc(record.get("nroRucReceptorDesc")),
        "series": clean_field(record.get("nroSerie")),
        "number": clean_field(record.get("nroFactura")),
        "full_number": clean_field(record.get("nroFacturaDesc")),
        "issue_date": issue_date,
        "period": period,
        "currency": clean_field(record.get("codigoMoneda")),
        "currency_symbol": clean_field(record.get("codigoMonedaDesc")),
        "total_amount": parse_amount(record.get("importeTotalDesc")),
        "status": clean_field(record.get("estadoDesc")),
        "is_cancelled": str(record.get("ind_anulado") or "0") not in ("0", ""),
        "is_rejected": is_rejected,
        "reject_date": clean_field(record.get("fechaRechazoDesc")),
        "references_document": clean_field(record.get("comprobantePorElQueSeEmite")),
        "xml_id": clean_field(record.get("numeroIdXml")),
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
