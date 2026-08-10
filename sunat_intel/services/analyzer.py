"""Per-message AI analysis with cached, auditable results.

Honesty rules enforced by prompt AND by post-validation:
* Amounts and legal deadlines are stored only with an explicit source.
* The mailbox ``expires_at`` is never treated as a legal deadline.
* Unavailable attachments are declared, not guessed around.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sunat_mailbox.models import ExtractionStatus, Message

from ..models import AnalysisStatus, Confidence, MessageAnalysis, Priority
from . import llm

logger = logging.getLogger(__name__)

# Bump when the prompt/schema changes so every message is re-analyzed once.
ANALYSIS_VERSION = "2"

MAX_ATTACHMENT_CHARS = 8000

UNAVAILABLE_NOTE = (
    "No se pudo completar el análisis porque el documento no está disponible "
    "mediante la integración actual."
)

ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "comm_type": {
            "type": "string",
            "description": "Tipo de comunicación, p.ej. 'Resolución de multa', "
                           "'Orden de pago', 'Aviso informativo'.",
        },
        "priority": {
            "type": "string",
            "enum": ["critical", "high", "medium", "informational"],
        },
        "requires_action": {"type": "boolean"},
        "summary": {"type": "string"},
        "why_it_matters": {"type": "string"},
        "next_action": {"type": ["string", "null"]},
        "tribute": {"type": ["string", "null"]},
        "tax_period": {
            "type": ["string", "null"],
            "description": "Formato YYYY-MM si hay evidencia.",
        },
        "references": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Números de resolución, orden de pago, expediente o "
                           "valor citados textualmente en el asunto o documentos.",
        },
        "amount": {"type": ["number", "null"]},
        "amount_source": {"type": ["string", "null"]},
        "legal_deadline": {
            "type": ["string", "null"],
            "description": "Fecha YYYY-MM-DD solo si un documento la evidencia.",
        },
        "deadline_source": {"type": ["string", "null"]},
        "missing_info": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "comm_type", "priority", "requires_action", "summary", "why_it_matters",
        "next_action", "tribute", "tax_period", "references", "amount",
        "amount_source", "legal_deadline", "deadline_source", "missing_info",
        "confidence", "sources",
    ],
}

SYSTEM_PROMPT = """Eres un analista tributario que procesa comunicaciones del \
buzón electrónico SOL de SUNAT para el equipo directivo de una PYME peruana. \
Analiza UNA comunicación y devuelve el JSON pedido.

Reglas estrictas:
- No inventes información. Todo dato relevante debe provenir del asunto o de \
los documentos adjuntos incluidos; indica la fuente en `sources` \
("asunto" o "adjunto:<nombre>").
- `amount` solo si el monto aparece expresamente en una fuente; en ese caso \
`amount_source` dice dónde. Si no hay monto, ambos null.
- `legal_deadline` solo si una fuente evidencia una fecha legal (plazo de \
descargo, fecha de comparecencia, vencimiento de fraccionamiento…). La \
vigencia del mensaje en el buzón NO es un plazo legal.
- Si falta el documento o información clave, decláralo en `missing_info` y \
baja `confidence`.
- Prioridad: `critical` = riesgo económico inminente o plazo legal en curso \
(ejecución coactiva activa, embargo, multa no resuelta con plazo); `high` = \
requiere gestión pronta (esquela, requerimiento, orden de pago abierta); \
`medium` = requiere verificación sin urgencia; `informational` = no requiere \
acción. Una resolución de CONCLUSIÓN de cobranza o un archivo de expediente \
REDUCE el riesgo: es `informational` o `medium`, nunca crítica.
- Un mensaje informativo (reportes, constancias, avisos de generación de \
registros) lleva `requires_action` false y no propone tareas innecesarias.
- `references`: copia los números de documento del caso tal como aparecen \
(p.ej. "023-002-2905092"), incluyendo los citados dentro del adjunto que \
correspondan a otros documentos del mismo asunto (resolución que concluye una \
orden de pago, etc.). No incluyas RUC, fechas ni normas legales generales \
(resoluciones de superintendencia, decretos, leyes): solo documentos emitidos \
para este contribuyente.
- `summary` (2-3 frases) y `why_it_matters` (1 frase) en español claro para un \
CEO, sin jerga innecesaria. `next_action` es una recomendación concreta o null \
si no aplica; nunca ejecutes ni des por hecha la acción.
- No emitas conclusiones legales definitivas."""


def _attachment_sections(message: Message) -> tuple[list[str], list[str]]:
    """Return (document sections, availability notes) for the prompt."""
    sections: list[str] = []
    notes: list[str] = []
    for attachment in message.attachments.all():
        name = attachment.file_name or attachment.display_name or "documento"
        if attachment.extraction_status == ExtractionStatus.EXTRACTED and attachment.text_content:
            text = attachment.text_content[:MAX_ATTACHMENT_CHARS]
            sections.append(f"--- adjunto:{name} ---\n{text}")
        else:
            notes.append(
                f"El adjunto {name} no está disponible mediante la integración actual."
            )
    return sections, notes


def build_prompt(message: Message) -> str:
    published = message.published_at.isoformat() if message.published_at else "desconocida"
    parts = [
        f"RUC: {message.taxpayer_id}",
        f"Asunto: {message.subject}",
        f"Fecha de publicación en el buzón: {published}",
        f"Tipo SUNAT: {message.get_message_type_display()}",
        f"Cantidad de adjuntos: {message.attachment_count}",
    ]
    sections, notes = _attachment_sections(message)
    parts.extend(notes)
    parts.extend(sections)
    return "\n\n".join(parts)


def fingerprint(message: Message) -> str:
    """Cache key: reprocess only when content, attachments or method change."""
    hasher = hashlib.sha256()
    hasher.update(ANALYSIS_VERSION.encode())
    hasher.update(llm.INTEL_MODEL.encode())
    hasher.update((message.subject or "").encode())
    hasher.update(str(message.published_at or "").encode())
    for attachment in message.attachments.all():
        hasher.update((attachment.checksum or attachment.file_name or "").encode())
        hasher.update(attachment.extraction_status.encode())
        hasher.update(str(len(attachment.text_content or "")).encode())
    return hasher.hexdigest()


def _parse_amount(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_period(value: Any) -> str:
    if not value:
        return ""
    match = re.fullmatch(r"(20\d{2})-(0[1-9]|1[0-2])", str(value))
    return match.group(0) if match else ""


def _valid_choice(value: Any, choices, default: str) -> str:
    return value if value in choices.values else default


def apply_result(analysis: MessageAnalysis, data: dict[str, Any]) -> None:
    """Map validated LLM output onto the model. Sourceless amounts/deadlines
    are dropped — the rule is enforced here, not only in the prompt."""
    analysis.comm_type = (data.get("comm_type") or "")[:120]
    analysis.priority = _valid_choice(
        data.get("priority"), Priority, Priority.INFORMATIONAL
    )
    analysis.requires_action = bool(data.get("requires_action"))
    analysis.summary = data.get("summary") or ""
    analysis.why_it_matters = data.get("why_it_matters") or ""
    analysis.next_action = data.get("next_action") or ""
    analysis.tribute = (data.get("tribute") or "")[:60]
    analysis.tax_period = _parse_period(data.get("tax_period"))
    analysis.references = [
        str(r).strip() for r in data.get("references") or [] if str(r).strip()
    ]

    amount = _parse_amount(data.get("amount"))
    amount_source = (data.get("amount_source") or "")[:255]
    analysis.amount = amount if (amount is not None and amount_source) else None
    analysis.amount_source = amount_source if analysis.amount is not None else ""

    deadline = _parse_date(data.get("legal_deadline"))
    deadline_source = (data.get("deadline_source") or "")[:255]
    analysis.legal_deadline = deadline if (deadline and deadline_source) else None
    analysis.deadline_source = deadline_source if analysis.legal_deadline else ""

    analysis.missing_info = [str(m) for m in data.get("missing_info") or []]
    analysis.confidence = _valid_choice(
        data.get("confidence"), Confidence, Confidence.LOW
    )
    analysis.sources = [str(s) for s in data.get("sources") or []]
    analysis.raw_response = data
    analysis.status = AnalysisStatus.DONE
    analysis.error = ""


def analyze_message(message: Message) -> MessageAnalysis:
    """Analyze one message, storing the result (or the failure) for audit."""
    analysis, _ = MessageAnalysis.objects.get_or_create(message=message)
    analysis.fingerprint = fingerprint(message)
    analysis.model_name = llm.INTEL_MODEL
    try:
        data = llm.structured_completion(
            SYSTEM_PROMPT, build_prompt(message), "message_analysis", ANALYSIS_SCHEMA
        )
        apply_result(analysis, data)
    except Exception as exc:  # the failure itself is the stored record
        logger.exception("Analysis failed for message %s", message.pk)
        analysis.status = AnalysisStatus.FAILED
        analysis.error = str(exc)[:2000]
    analysis.save()
    return analysis


def pending_messages(taxpayer_id: str | None = None, force: bool = False):
    """Messages that need (re)analysis: new, failed, or stale fingerprint.

    ``taxpayer_id`` acota a una empresa. Se deja opcional porque las
    herramientas de operación (el comando de gestión) recorren la base
    entera, pero el flujo que dispara la sincronización SIEMPRE lo pasa: sin
    él, sincronizar una empresa gastaría llamadas al modelo analizando los
    mensajes de todas las demás.
    """
    messages = Message.objects.prefetch_related("attachments").order_by("published_at")
    if taxpayer_id:
        messages = messages.for_taxpayer(taxpayer_id)
    for message in messages:
        analysis = getattr(message, "analysis", None)
        needs_run = (
            force
            or analysis is None
            or analysis.status != AnalysisStatus.DONE
            or analysis.fingerprint != fingerprint(message)
        )
        if needs_run:
            yield message


def analyze_pending(
    taxpayer_id: str | None = None,
    limit: int | None = None,
    force: bool = False,
) -> dict[str, int]:
    """Run the analysis over every message that needs it, for one empresa."""
    done = failed = 0
    for index, message in enumerate(
        pending_messages(taxpayer_id=taxpayer_id, force=force)
    ):
        if limit is not None and index >= limit:
            break
        analysis = analyze_message(message)
        if analysis.status == AnalysisStatus.DONE:
            done += 1
        else:
            failed += 1
    return {"analyzed": done, "failed": failed}
