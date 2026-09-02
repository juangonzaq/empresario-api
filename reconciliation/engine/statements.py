"""Bank statement PDFs → bank movements.

Two hard parts handled here:

1. **The password.** Peruvian bank statements ship password-protected. The
   default follows the convention the company set: the 8 RUC digits *after the
   2nd and without the last* — i.e. ``RUC[2:10]``. A per-upload override is
   accepted and never stored.
2. **The parsing.** Layouts differ per bank, so the parser is heuristic and
   deliberately conservative: it extracts one movement per line that has a date
   and an amount, guesses credit/debit from a keyword lexicon and sign, and
   leaves the category as ``unidentified`` so the classifier and the user —
   whose decision always wins — take it from there. Every statement keeps its
   raw text, so a better parser can be swapped in without re-uploading.
"""

from __future__ import annotations

import datetime
import io
import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from pypdf import PdfReader

from ..models import BankMovement, MovementKind

logger = logging.getLogger(__name__)

# ---- password ----------------------------------------------------------

def statement_password(ruc: str) -> str:
    """The default statement password: RUC digits after the 2nd, without the
    last one. ``20604442533`` → ``60444253``."""
    digits = re.sub(r"\D", "", ruc or "")
    return digits[2:10]


# ---- text extraction ---------------------------------------------------

class WrongPassword(Exception):
    pass


def _sanitize_pdf(data: bytes) -> bytes:
    """Recorta el PDF real de adentro del envoltorio del banco.

    Caso real (BCP, 2026-09): el EECC descargado empieza con ``$BOP$`` y
    termina en ``$EOP$$BOP$$EOP$`` — marcadores del spool de impresión del
    banco alrededor del PDF. Esos bytes extra corren TODOS los offsets
    internos (xref) y tanto pypdf como pdfminer concluyen «no hay /Root».
    Del primer ``%PDF`` al último ``%%EOF`` está el documento de verdad."""
    start = data.find(b"%PDF")
    if start <= 0:
        return data  # ya empieza en %PDF, o ni siquiera es un PDF: que el parser lo diga
    end = data.rfind(b"%%EOF")
    return data[start:end + 5] if end > start else data[start:]


def extract_text(data: bytes, passwords: list[str]) -> str:
    """Open (decrypting if needed) and return the concatenated text.

    pypdf primero; si el PDF tiene la estructura rota —los EECC bancarios
    suelen traer la tabla xref dañada y pypdf muere con «Cannot find Root
    object» aunque cualquier visor lo abra—, cae a pdfminer, que reconstruye
    el índice escaneando los objetos. ``WrongPassword`` si ninguna contraseña
    candidata lo abre."""
    data = _sanitize_pdf(data)
    try:
        return _extract_pypdf(data, passwords)
    except WrongPassword:
        raise
    except Exception:  # noqa: BLE001 — estructura rota: probar el lector tolerante
        logger.warning(
            "pypdf no pudo leer el estado de cuenta; reintentando con pdfminer",
            exc_info=True,
        )
        return _extract_pdfminer(data, passwords)


def _extract_pypdf(data: bytes, passwords: list[str]) -> str:
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        opened = False
        for pw in [p for p in passwords if p]:
            try:
                if reader.decrypt(pw) > 0:
                    opened = True
                    break
            except Exception:  # noqa: BLE001 — some ciphers raise instead of returning 0
                continue
        if not opened:
            raise WrongPassword("Ninguna contraseña abrió el PDF.")
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _extract_pdfminer(data: bytes, passwords: list[str]) -> str:
    from pdfminer.high_level import extract_text as pdfminer_extract
    from pdfminer.pdfdocument import PDFPasswordIncorrect

    ultimo: Exception | None = None
    # Primero sin contraseña: un PDF roto no siempre es un PDF cifrado.
    for pw in ["", *[p for p in passwords if p]]:
        try:
            return pdfminer_extract(io.BytesIO(data), password=pw)
        except PDFPasswordIncorrect as exc:
            ultimo = exc
    raise WrongPassword("Ninguna contraseña abrió el PDF.") from ultimo


# ---- parsing -----------------------------------------------------------

DATE_RE = re.compile(r"\b(\d{2})[/-](\d{2})(?:[/-](\d{2,4}))?\b")
AMOUNT_RE = re.compile(r"-?\(?\d{1,3}(?:[,\.]\d{3})*[,\.]\d{2}\)?-?")

CREDIT_WORDS = re.compile(
    r"ABONO|DEP[OÓ]SITO|DEPOSITO|TRANSFERENCIA\s+RECIBID|INTERES|INTER[EÉ]S\s+GANAD|"
    r"DEVOLUC|REEMBOLSO|EXTORNO|N/CR|NOTA\s+DE\s+ABONO|INGRESO|HABER|YAPE\s+RECIB|PLIN\s+RECIB",
    re.I,
)
DEBIT_WORDS = re.compile(
    r"CARGO|RETIRO|PAGO|COMPRA|COMISI[OÓ]N|IMPUESTO|ITF|MANTENIMIENTO|TRANSFERENCIA\s+A\s|"
    r"GIRO|CHEQUE|N/DB|NOTA\s+DE\s+CARGO|DEBITO|D[EÉ]BITO|PORTES|SUNAT",
    re.I,
)


@dataclass
class ParsedMovement:
    date: datetime.date
    amount: Decimal
    kind: str
    description: str
    balance: Decimal | None
    operation_number: str


def _to_decimal(token: str) -> Decimal | None:
    neg = token.strip().startswith("-") or token.strip().endswith("-") or ("(" in token and ")" in token)
    cleaned = re.sub(r"[()\-\s]", "", token)
    # Normalize thousands/decimal: last separator is the decimal one.
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".") if cleaned.count(",") == 1 and len(cleaned.split(",")[-1]) == 2 else cleaned.replace(",", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -value if neg else value


def _year(y: str | None, default_year: int) -> int:
    if not y:
        return default_year
    return int(y) if len(y) == 4 else 2000 + int(y)


def _kind(description: str, amount: Decimal) -> tuple[str, float]:
    if amount < 0:
        return MovementKind.DEBIT, 0.7
    if CREDIT_WORDS.search(description):
        return MovementKind.CREDIT, 0.6
    if DEBIT_WORDS.search(description):
        return MovementKind.DEBIT, 0.6
    return MovementKind.DEBIT, 0.3  # unknown: default debit, low confidence


def parse_statement(text: str, default_year: int | None = None) -> list[ParsedMovement]:
    default_year = default_year or datetime.date.today().year
    movements: list[ParsedMovement] = []
    for raw in text.splitlines():
        line = raw.strip()
        if len(line) < 8:
            continue
        date_match = DATE_RE.search(line)
        if not date_match:
            continue
        amounts = AMOUNT_RE.findall(line)
        # Drop the date itself if it slipped into the amount matches.
        amounts = [a for a in amounts if "." in a or "," in a]
        if not amounts:
            continue
        try:
            day, month = int(date_match.group(1)), int(date_match.group(2))
            date = datetime.date(_year(date_match.group(3), default_year), month, day)
        except ValueError:
            continue
        values = [v for v in (_to_decimal(a) for a in amounts) if v is not None]
        if not values:
            continue
        # Heuristic: last number tends to be the running balance when there are
        # 2+; the movement is the previous one. With a single number, it is the
        # movement.
        if len(values) >= 2:
            amount, balance = values[-2], values[-1]
        else:
            amount, balance = values[0], None
        description = line[date_match.end():].strip()
        for a in amounts:
            description = description.replace(a, "").strip()
        description = re.sub(r"\s{2,}", " ", description)[:300]
        op = ""
        op_match = re.search(r"\bOP(?:ERAC(?:ION|IÓN)?)?\.?\s*[:#]?\s*(\d{4,})", line, re.I)
        if op_match:
            op = op_match.group(1)[:40]
        kind, _conf = _kind(description, amount)
        movements.append(ParsedMovement(date, abs(amount), kind, description, balance, op))
    return movements


# ---- persistence -------------------------------------------------------

def import_statement(statement) -> dict[str, Any]:
    """Parse a saved ``BankStatement`` and create its movements. Skips rows that
    already exist (same date, amount, kind, description) so re-processing is
    idempotent. Returns counts and the detected period."""
    from ..models import StatementStatus

    default_pw = statement_password(statement.account_ruc)
    override = getattr(statement, "_password_override", "") or ""
    data = statement.file.read()
    text = extract_text(data, [override, default_pw])
    parsed = parse_statement(text)

    created = 0
    for m in parsed:
        exists = BankMovement.objects.filter(
            account_ruc=statement.account_ruc, date=m.date, amount=m.amount,
            kind=m.kind, description=m.description, currency=statement.currency,
        ).exists()
        if exists:
            continue
        BankMovement.objects.create(
            account_ruc=statement.account_ruc, date=m.date,
            period=f"{m.date.year}{m.date.month:02d}", bank=statement.bank,
            bank_account=statement.bank_account, currency=statement.currency,
            kind=m.kind, amount=m.amount, description=m.description,
            operation_number=m.operation_number, source="statement", statement=statement,
        )
        created += 1

    dates = [m.date for m in parsed]
    statement.movement_count = created
    statement.period_from = min(dates) if dates else None
    statement.period_to = max(dates) if dates else None
    statement.status = StatementStatus.PARSED
    statement.error = ""
    statement.save(update_fields=["movement_count", "period_from", "period_to", "status", "error", "updated_at"])
    return {"created": created, "parsed": len(parsed),
            "period_from": statement.period_from, "period_to": statement.period_to}
