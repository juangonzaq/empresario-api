"""AI explanation layer — strictly on top of the deterministic results.

The model receives numbers the engine already computed and returns prose: what
the differences are, what to review first, possible benign causes. It never
computes taxes and never labels anything as evasion; the schema and the system
prompt enforce the vocabulary («inconsistencia», «requiere revisión»).
"""

from __future__ import annotations

import json
from typing import Any

from ..models import ConsistencyScore, ReconciliationRun

SYSTEM_PROMPT = (
    "Eres el analista de conciliación tributaria de EMPRESARIO, hablando con el dueño de una "
    "empresa peruana. Recibes resultados YA CALCULADOS por un motor determinístico (nunca "
    "recalcules ni inventes montos). Tu trabajo: explicar las diferencias en castellano claro, "
    "priorizar qué revisar, proponer causas posibles (transferencias propias, cobranzas de "
    "otros periodos, boletas consolidadas, desfases de registro) y resumir el riesgo. "
    "PROHIBIDO: afirmar o insinuar evasión, omisión, infracción o ventas no declaradas. "
    "Usa siempre: «inconsistencia», «diferencia», «pendiente de clasificar», «requiere revisión»."
)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string", "description": "3-5 frases para el dueño."},
        "priorities": {
            "type": "array", "maxItems": 5,
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "why": {"type": "string"},
                    "possible_causes": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                },
                "required": ["title", "why", "possible_causes"],
            },
        },
    },
    "required": ["summary", "priorities"],
}


def payload_for(run: ReconciliationRun) -> dict[str, Any]:
    from finance_analytics.models import FinanceAlert

    score = ConsistencyScore.objects.filter(account_ruc=run.account_ruc, period=run.period).first()
    alerts = FinanceAlert.objects.filter(
        account_ruc=run.account_ruc, period=run.period, dedup_key__startswith="recon:",
    ).open()
    return {
        "periodo": run.period,
        "totales": run.totals,
        "score": score.score if score else None,
        "desglose_score": score.breakdown if score else [],
        "alertas_abiertas": [
            {"titulo": a.title, "detalle": a.explanation, "severidad": a.severity,
             "monto": float(a.amount) if a.amount is not None else None}
            for a in alerts
        ],
    }


def explain(run: ReconciliationRun) -> dict[str, Any]:
    from sunat_intel.services import llm

    result = llm.structured_completion(
        SYSTEM_PROMPT,
        json.dumps(payload_for(run), ensure_ascii=False, default=str),
        "reconciliation_explanation",
        SCHEMA,
    )
    run.ai_explanation = {**result, "model": llm.INTEL_MODEL}
    run.save(update_fields=["ai_explanation", "updated_at"])
    return run.ai_explanation
