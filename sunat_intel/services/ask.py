"""«Pregúntale a VIGÍA sobre SUNAT» — grounded Q&A over the company's data.

The model receives ONLY this company's analyzed messages, cases and the SUNAT
compliance profile, and must answer from that context, citing sources. It is
instructed to say when the information is insufficient and to avoid definitive
legal conclusions. Only the compact context below is sent to OpenAI — never
raw payloads or credentials.
"""

from __future__ import annotations

from typing import Any

from ..models import AnalysisStatus, Case, MessageAnalysis, VigiaMessage
from . import llm

# Turns of chat history replayed to the model for follow-up questions.
HISTORY_TURNS = 10

ASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "answer": {"type": "string"},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string", "enum": ["message", "case", "compliance"]},
                    "id": {"type": "string"},
                    "label": {"type": "string"},
                },
                "required": ["kind", "id", "label"],
            },
        },
        "has_sufficient_info": {"type": "boolean"},
    },
    "required": ["answer", "sources", "has_sufficient_info"],
}

SYSTEM_PROMPT = """Eres VIGÍA, el asistente del módulo SUNAT de una plataforma \
de gestión para PYMEs peruanas. Respondes preguntas del equipo directivo sobre \
las comunicaciones de SUNAT de SU empresa.

Reglas estrictas:
- Responde ÚNICAMENTE con la información del contexto proporcionado. No uses \
conocimiento externo para afirmar hechos sobre la empresa.
- Cita en `sources` cada mensaje (kind "message", id = el uuid indicado), caso \
(kind "case") o dato del perfil de cumplimiento (kind "compliance", id \
"profile") que sustente tu respuesta.
- Si el contexto no alcanza para responder, dilo claramente y marca \
`has_sufficient_info` en false.
- No emitas conclusiones legales definitivas ni recomendaciones de pago o \
impugnación como decisión tomada; puedes describir opciones y sugerir \
consultarlas con el contador o asesor legal.
- Los montos que menciones deben aparecer en el contexto; no estimes ni sumes \
montos que no estén.
- Responde en español claro y ejecutivo, breve pero completo."""


def _message_lines(taxpayer_id: str) -> list[str]:
    lines = []
    analyses = (
        MessageAnalysis.objects.filter(
            status=AnalysisStatus.DONE, message__taxpayer_id=taxpayer_id
        )
        .select_related("message")
        .order_by("-message__published_at")
    )
    for a in analyses:
        m = a.message
        published = m.published_at.date().isoformat() if m.published_at else "s/f"
        parts = [
            f"[mensaje {m.id}] {published} | {a.comm_type or 'Comunicación'}",
            f"prioridad {a.priority}",
            f"acción requerida: {'sí' if a.requires_action else 'no'}",
            a.summary,
        ]
        if a.tribute:
            parts.append(f"tributo {a.tribute}")
        if a.tax_period:
            parts.append(f"periodo {a.tax_period}")
        if a.amount is not None:
            parts.append(f"monto S/ {a.amount} (fuente: {a.amount_source})")
        if a.legal_deadline:
            parts.append(f"plazo {a.legal_deadline} (fuente: {a.deadline_source})")
        if a.references:
            parts.append("refs " + ", ".join(a.references))
        lines.append(" | ".join(p for p in parts if p))
    return lines


def _case_lines(taxpayer_id: str) -> list[str]:
    lines = []
    for c in Case.objects.filter(taxpayer_id=taxpayer_id):
        parts = [
            f"[caso {c.id}] {c.title}",
            f"riesgo {c.risk}",
            f"estado {c.get_status_display()}",
            f"responsable {c.responsible or 'sin asignar'}",
            c.summary,
        ]
        if c.exposure_amount is not None:
            parts.append(f"exposición S/ {c.exposure_amount} ({c.exposure_source})")
        if c.deadline:
            parts.append(f"plazo {c.deadline}")
        if c.next_action:
            parts.append(f"próxima acción: {c.next_action}")
        lines.append(" | ".join(p for p in parts if p))
    return lines


def _compliance_lines(taxpayer_id: str) -> list[str]:
    """Latest SUNAT compliance profile, so questions like «¿por qué tengo
    perfil D?» can be answered from real findings."""
    try:
        from compliance_profile.models import ComplianceRating
        from compliance_profile.services import findings as cf
    except ImportError:  # pragma: no cover — app always present in this project
        return []
    rating = (
        ComplianceRating.objects.filter(taxpayer_id=taxpayer_id)
        .exclude(rating="")
        .order_by("-period", "-execution_period", "-loaded_at")
        .first()
    )
    if rating is None:
        return []
    lines = [
        f"[compliance profile] Perfil de cumplimiento SUNAT: categoría "
        f"{rating.rating} · periodo de evaluación hasta {rating.period}"
    ]
    for finding in cf.build_findings(rating):
        lines.append(
            "[compliance profile] Hallazgo: "
            f"{finding.get('title')} | severidad {finding.get('severity_label')}"
        )
    return lines


def build_context(taxpayer_id: str) -> str:
    sections = [
        "## Mensajes analizados del buzón SUNAT",
        *(_message_lines(taxpayer_id) or ["(sin mensajes analizados)"]),
        "\n## Casos",
        *(_case_lines(taxpayer_id) or ["(sin casos)"]),
        "\n## Perfil de cumplimiento",
        *(_compliance_lines(taxpayer_id) or ["(sin perfil disponible)"]),
    ]
    return "\n".join(sections)


def ask(taxpayer_id: str, question: str) -> dict[str, Any]:
    """Answer a question, replaying recent chat turns for follow-ups, and
    persist both sides of the exchange (this is also the consultation log)."""
    history = list(
        VigiaMessage.objects.filter(taxpayer_id=taxpayer_id).order_by(
            "-created_at"
        )[:HISTORY_TURNS]
    )[::-1]

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend({"role": h.role, "content": h.content} for h in history)
    messages.append({
        "role": "user",
        "content": (
            f"Contexto actualizado de la empresa (RUC {taxpayer_id}):\n\n"
            f"{build_context(taxpayer_id)}\n\n"
            f"Pregunta del usuario: {question}"
        ),
    })
    result = llm.structured_messages(messages, "vigia_answer", ASK_SCHEMA)

    # Both turns are stored only after a successful answer, so a failed call
    # can be retried without duplicating the question in the history.
    VigiaMessage.objects.create(
        taxpayer_id=taxpayer_id, role="user", content=question
    )
    assistant = VigiaMessage.objects.create(
        taxpayer_id=taxpayer_id,
        role="assistant",
        content=result.get("answer", ""),
        sources=result.get("sources", []),
        has_sufficient_info=result.get("has_sufficient_info"),
    )
    return {**result, "message_id": str(assistant.id)}
