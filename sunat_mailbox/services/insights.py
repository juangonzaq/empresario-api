"""Functional classification for mailbox messages.

The rules here are deliberately conservative: category, priority and action
status are derived only from verifiable patterns in the stored subject and
payloads. Nothing is inferred beyond what SUNAT actually sent — in particular,
``expires_at`` is availability of the message, never a legal deadline.
"""

from __future__ import annotations

import html
import re
import urllib.parse
from typing import Any

from ..models import Attachment, ExtractionStatus, Message, MessageType

SOURCE = "SUNAT"

# Priority buckets used by the card and the list badges.
PRIORITY_URGENT = "urgent"
PRIORITY_ATTENTION = "attention"
PRIORITY_INFORMATIONAL = "informational"

# Action statuses. "recommended" means no mandatory action was detected but a
# verification is suggested; the recommendation text says which one.
ACTION_REQUIRED = "required"
ACTION_RECOMMENDED = "recommended"
ACTION_NONE = "none"

ACTION_LABEL = {
    ACTION_REQUIRED: "Requiere acción",
    ACTION_RECOMMENDED: "Revisión recomendada",
    ACTION_NONE: "No se detectó una acción obligatoria",
}

# Ordered catalog: the first pattern that matches the subject wins. Each entry
# carries the executive-facing label, the priority bucket, the action status
# and an optional recommendation shown in the "¿Requiere acción?" section.
CATEGORY_RULES: list[dict[str, Any]] = [
    {
        "pattern": r"ejecuci[oó]n coactiva|resoluci[oó]n coactiva|cobranza coactiva|rca ingreso recaudaci[oó]n",
        "key": "cobranza_coactiva",
        "label": "Cobranza coactiva",
        "priority": PRIORITY_URGENT,
        "action": ACTION_REQUIRED,
        "recommendation": "Revisar la resolución con el contador y validar el estado de la deuda en cobranza.",
        "summary": "SUNAT notificó una resolución vinculada a un procedimiento de cobranza coactiva.",
    },
    {
        "pattern": r"resoluci[oó]n de multa",
        "key": "multa",
        "label": "Multa",
        "priority": PRIORITY_URGENT,
        "action": ACTION_REQUIRED,
        "recommendation": "Revisar la resolución de multa y evaluar el pago o la impugnación dentro del plazo legal.",
        "summary": "SUNAT notificó una resolución de multa.",
    },
    {
        "pattern": r"orden de pago",
        "key": "orden_pago",
        "label": "Orden de pago",
        "priority": PRIORITY_URGENT,
        "action": ACTION_REQUIRED,
        "recommendation": "Verificar la deuda indicada en la orden de pago y coordinar su regularización.",
        "summary": "SUNAT notificó una orden de pago.",
    },
    {
        "pattern": r"esquela",
        "key": "esquela",
        "label": "Esquela",
        "priority": PRIORITY_URGENT,
        "action": ACTION_REQUIRED,
        "recommendation": "Atender la esquela dentro del plazo indicado en el documento notificado.",
        "summary": "SUNAT notificó una esquela que requiere atención.",
    },
    {
        "pattern": r"pedido de informaci[oó]n|requerimiento",
        "key": "requerimiento",
        "label": "Requerimiento de información",
        "priority": PRIORITY_URGENT,
        "action": ACTION_REQUIRED,
        "recommendation": "Preparar la información solicitada y responder dentro del plazo indicado.",
        "summary": "SUNAT solicitó información mediante un requerimiento.",
    },
    {
        "pattern": r"beneficiario final",
        "key": "declaracion_informativa",
        "label": "Declaración informativa",
        "priority": PRIORITY_ATTENTION,
        "action": ACTION_RECOMMENDED,
        "recommendation": "Verificar si la empresa está comprendida en la obligación informada y su fecha de presentación.",
        "summary": "SUNAT comunicó una obligación de declaración informativa.",
    },
    {
        "pattern": r"obligado a la presentaci[oó]n",
        "key": "aviso_declaracion",
        "label": "Aviso de declaración",
        "priority": PRIORITY_ATTENTION,
        "action": ACTION_RECOMMENDED,
        "recommendation": "Verificar que la declaración del periodo indicado esté presentada.",
        "summary": "SUNAT recordó la obligación de presentar una declaración.",
    },
    {
        "pattern": r"vencimiento",
        "key": "aviso_vencimiento",
        "label": "Aviso de vencimiento",
        "priority": PRIORITY_ATTENTION,
        "action": ACTION_RECOMMENDED,
        "recommendation": "Confirmar que la obligación mencionada se presente antes de la fecha de vencimiento.",
        "summary": "SUNAT avisó sobre un vencimiento próximo.",
    },
    {
        "pattern": r"generaci[oó]n de (registro )?rvie|rvie y rce",
        "key": "registros_electronicos",
        "label": "Registros electrónicos",
        "priority": PRIORITY_INFORMATIONAL,
        "action": ACTION_NONE,
        "recommendation": "Verificar que los registros generados sean correctos.",
        "summary": "SUNAT informa que generó los registros RVIE y RCE.",
    },
    {
        "pattern": r"resumen de comprobantes|comprobantes de pago",
        "key": "comprobantes",
        "label": "Comprobantes electrónicos",
        "priority": PRIORITY_INFORMATIONAL,
        "action": ACTION_NONE,
        "recommendation": None,
        "summary": "SUNAT envió un reporte informativo sobre comprobantes electrónicos.",
    },
    {
        "pattern": r"expediente mpv|se registr[oó] expediente",
        "key": "tramite",
        "label": "Trámite",
        "priority": PRIORITY_INFORMATIONAL,
        "action": ACTION_NONE,
        "recommendation": None,
        "summary": "SUNAT registró un trámite o expediente presentado por la empresa.",
    },
]

FALLBACK_NOTIFICATION = {
    "key": "notificacion",
    "label": "Notificación",
    "priority": PRIORITY_ATTENTION,
    "action": ACTION_RECOMMENDED,
    "recommendation": "Revisar el documento notificado en el buzón electrónico.",
    "summary": "SUNAT depositó una notificación en el buzón electrónico.",
}

FALLBACK_MESSAGE = {
    "key": "informativo",
    "label": "Informativo",
    "priority": PRIORITY_INFORMATIONAL,
    "action": ACTION_NONE,
    "recommendation": None,
    "summary": "SUNAT envió una comunicación informativa.",
}

MONTH_NAMES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def classify(subject: str, message_type: int) -> dict[str, Any]:
    """Return the catalog entry that matches the subject."""
    lowered = (subject or "").lower()
    for rule in CATEGORY_RULES:
        if re.search(rule["pattern"], lowered):
            return rule
    if message_type == MessageType.NOTIFICATION:
        return FALLBACK_NOTIFICATION
    return FALLBACK_MESSAGE


def clean_subject(subject: str) -> str:
    """Strip the redundant 'ASUNTO:' prefix SUNAT prepends to notifications."""
    return re.sub(r"^\s*asunto\s*:\s*", "", subject or "", flags=re.IGNORECASE).strip()


def extract_tax_period(subject: str) -> str | None:
    """Find a tax period in the subject, returned as ``YYYY-MM``."""
    text = subject or ""
    compact = re.search(r"\b(20\d{2})(0[1-9]|1[0-2])\b", text)
    if compact:
        return f"{compact.group(1)}-{compact.group(2)}"
    dashed = re.search(r"\b(0[1-9]|1[0-2])-(20\d{2})\b", text)
    if dashed:
        return f"{dashed.group(2)}-{dashed.group(1)}"
    return None


def format_tax_period(period: str | None) -> str | None:
    if not period:
        return None
    year, _, month = period.partition("-")
    try:
        return f"{MONTH_NAMES[int(month) - 1]} de {year}"
    except (ValueError, IndexError):
        return period


def normalized_sender(message: Message) -> str:
    """The mailbox sender is SUNAT; ``codUsremisor`` confirms it when present.

    ``sender_name`` is not usable here: SUNAT fills it with the taxpayer's own
    business name.
    """
    code = (message.list_payload or {}).get("codUsremisor")
    if isinstance(code, str) and code.strip():
        return code.strip()
    return SOURCE


BR_SPLIT = re.compile(r"<br\\?/?>", re.IGNORECASE)
DOC_LIST = re.compile(r'listaDocumentos"\s*:\s*"([^"]*)')


def expected_documents(detail_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Parse ``listaDocumentos`` out of the raw detail payload, grouped.

    The list arrives URL-encoded inside ``msjMensaje``; it is decoded and split,
    never rewritten, so every document name is exactly what SUNAT announced.
    """
    if not detail_payload:
        return []
    raw = detail_payload.get("msjMensaje") or detail_payload.get("url") or ""
    if not isinstance(raw, str) or "listaDocumentos" not in raw:
        return []
    decoded = urllib.parse.unquote(raw)
    match = DOC_LIST.search(decoded)
    if not match:
        return []
    names = [
        html.unescape(part).strip()
        for part in BR_SPLIT.split(match.group(1))
        if part.strip()
    ]
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for name in names:
        group = _document_group(name)
        if group not in groups:
            groups[group] = []
            order.append(group)
        groups[group].append(name)
    return [{"group": group, "items": groups[group]} for group in order]


def _document_group(name: str) -> str:
    upper = name.upper()
    if "RVIE" in upper:
        return "RVIE"
    if "RCE" in upper or "NO DOMICILIADOS" in upper or "621" in upper:
        return "RCE"
    return "Otros"


UNAVAILABLE_NOTE = (
    "SUNAT informó la existencia del archivo, pero esta integración todavía "
    "no puede descargarlo."
)

UNAVAILABLE_FILE = ("unavailable", "No disponible", UNAVAILABLE_NOTE)

FILE_STATUS = {
    ExtractionStatus.EXTRACTED: ("available", "Disponible", None),
    ExtractionStatus.EMPTY: ("available", "Disponible (sin texto extraíble)", None),
    ExtractionStatus.PENDING: ("pending", "Pendiente de descarga", None),
    ExtractionStatus.UNSUPPORTED: UNAVAILABLE_FILE,
    ExtractionStatus.FAILED: UNAVAILABLE_FILE,
}


def normalize_files(attachments: list[Attachment]) -> list[dict[str, Any]]:
    """Executive view of the attachments: friendly label, size and status."""
    files = []
    for index, attachment in enumerate(attachments, start=1):
        status, status_label, note = FILE_STATUS.get(
            attachment.extraction_status, UNAVAILABLE_FILE
        )
        label = attachment.display_name or f"Documento adjunto {index}"
        files.append({
            "id": str(attachment.id),
            "label": label,
            "size": attachment.size_display or None,
            "status": status,
            "status_label": status_label,
            "note": note,
        })
    return files


def executive_summary(message: Message, rule: dict[str, Any], unavailable_files: int) -> str:
    """Short factual summary built from the catalog template, never from guesses."""
    base = rule["summary"]
    period_label = format_tax_period(extract_tax_period(message.subject))
    if period_label:
        base = f"{base.rstrip('.')}, periodo {period_label}."
    if unavailable_files:
        plural = "s" if unavailable_files != 1 else ""
        base += (
            f" El mensaje incluye {unavailable_files} archivo{plural} relacionado{plural}"
            " que no pudieron descargarse mediante la integración actual."
        )
    return base


def message_priority(message: Message, rule: dict[str, Any]) -> str:
    if message.is_urgent or rule["priority"] == PRIORITY_URGENT:
        return PRIORITY_URGENT
    return rule["priority"]


def list_fields(message: Message) -> dict[str, Any]:
    """Normalized fields shared by the list serializer and the card."""
    rule = classify(message.subject, message.message_type)
    return {
        "subject_clean": clean_subject(message.subject),
        "category": rule["key"],
        "category_label": rule["label"],
        "priority": message_priority(message, rule),
        "action_status": rule["action"],
        "action_label": ACTION_LABEL[rule["action"]],
        "tax_period": extract_tax_period(message.subject),
        "sender": SOURCE,
    }


def detail_insights(message: Message) -> dict[str, Any]:
    """The executive block for the message detail endpoint."""
    rule = classify(message.subject, message.message_type)
    attachments = list(message.attachments.all())
    files = normalize_files(attachments)
    unavailable = sum(1 for f in files if f["status"] == "unavailable")
    return {
        "source": SOURCE,
        "sender": normalized_sender(message),
        "subject_clean": clean_subject(message.subject),
        "category": rule["key"],
        "category_label": rule["label"],
        "priority": message_priority(message, rule),
        "tax_period": extract_tax_period(message.subject),
        "tax_period_label": format_tax_period(extract_tax_period(message.subject)),
        "summary": executive_summary(message, rule, unavailable),
        "action": {
            "status": rule["action"],
            "label": ACTION_LABEL[rule["action"]],
            "recommendation": rule["recommendation"],
        },
        "expected_documents": expected_documents(message.detail_payload),
        "files": files,
    }


def build_card(messages: list[Message], last_synced_at) -> dict[str, Any]:
    """Aggregate the mailbox into the home-dashboard card payload.

    ``ui_status`` reflects unread work only: urgent unread → critical, any
    unread → requires_review, otherwise ok. Reviewing a message in this app
    clears it from the counters even though SUNAT SOL still shows it unread.
    """
    unread = [m for m in messages if not m.is_reviewed]
    classified = [(m, classify(m.subject, m.message_type)) for m in unread]
    urgent = [m for m, rule in classified if message_priority(m, rule) == PRIORITY_URGENT]
    action_required = [m for m, rule in classified if rule["action"] == ACTION_REQUIRED]
    informational = [
        m for m, rule in classified
        if message_priority(m, rule) == PRIORITY_INFORMATIONAL
    ]

    top = _top_message(classified) or (messages[0] if messages else None)

    if urgent:
        ui_status = "critical"
    elif unread:
        ui_status = "requires_review"
    else:
        ui_status = "ok"

    return {
        "source": SOURCE,
        "ui_status": ui_status,
        "counts": {
            "total": len(messages),
            "unread": len(unread),
            "urgent": len(urgent),
            "action_required": len(action_required),
            "informational": len(informational),
        },
        "latest_message": _card_message(top) if top else None,
        "last_synced_at": last_synced_at,
    }


PRIORITY_RANK = {PRIORITY_URGENT: 0, PRIORITY_ATTENTION: 1, PRIORITY_INFORMATIONAL: 2}


def _top_message(classified: list[tuple[Message, dict[str, Any]]]) -> Message | None:
    """Most relevant unread message: highest priority first, then most recent."""
    if not classified:
        return None
    ranked = sorted(
        classified,
        key=lambda pair: (
            PRIORITY_RANK[message_priority(pair[0], pair[1])],
            -(pair[0].published_at.timestamp() if pair[0].published_at else 0),
        ),
    )
    return ranked[0][0]


def _card_message(message: Message) -> dict[str, Any]:
    fields = list_fields(message)
    files = normalize_files(list(message.attachments.all()))
    return {
        "id": str(message.id),
        **fields,
        "tax_period_label": format_tax_period(fields["tax_period"]),
        "published_at": message.published_at,
        "expires_at": message.expires_at,
        "is_read": message.is_read,
        "attachment_count": message.attachment_count,
        "attachments_unavailable": sum(1 for f in files if f["status"] == "unavailable"),
    }
