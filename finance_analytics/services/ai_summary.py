"""Monthly executive briefing: metrics in, briefing out.

The model NEVER computes totals nor touches invoice files — it receives the
aggregates already calculated by this app and only reads, prioritizes and
proposes. Results are cached per (period, metrics fingerprint), so tokens are
spent only when the underlying numbers change.

Two rules shape everything below:

* The payload sent to the model is **already humanized** — Spanish labels and
  amounts pre-formatted as ``S/ 1,234``. Internal field names and finding
  codes never leave this module, so they cannot reappear in the briefing.
* The output is **capped where it is produced**: 60 words of executive
  reading, 3 key changes, 3 attention items, 3 actions. The UI shows what it
  gets; it does not have to defend itself against a chatty model.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from typing import Any

from django.conf import settings
from django.utils import timezone

from sunat_intel.services import llm

from ..models import ActionStatus, FinanceAiSummary
from .consistency import consistency_analysis
from .cpe_summary import load_documents, purchases_summary, sales_summary
from .itf_summary import itf_summary
from .parties import customers_analysis, suppliers_analysis

# Sube cuando cambie la forma del briefing: invalida el caché anterior sin
# tocar los datos, porque entra en la huella.
BRIEFING_VERSION = "2"

MEANING_KEY = "qué es"

SUMMARY_WORD_LIMIT = 60
MAX_KEY_CHANGES = 3
MAX_ATTENTION = 3
MAX_ACTIONS = 3

# Responsables posibles. Cerrado a propósito: el modelo elige entre roles que
# existen en una PYME, no inventa cargos ni nombra personas.
OWNERS = ["Gerencia", "Contabilidad", "Administración", "Comercial", "Tesorería"]

# Horizonte de la acción, en días. El modelo propone el plazo; la fecha real
# la calcula el servidor, que sí sabe qué día es hoy.
MIN_DUE_DAYS = 1
MAX_DUE_DAYS = 60

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {
            "type": "string",
            "description": (
                f"Lectura ejecutiva de máximo {SUMMARY_WORD_LIMIT} palabras. "
                "Qué pasó y qué significa, no una lista de cifras."
            ),
        },
        "key_changes": {
            "type": "array",
            "description": (
                f"Hasta {MAX_KEY_CHANGES} cambios frente al mes anterior, una "
                "frase cada uno."
            ),
            "items": {"type": "string"},
        },
        "attention": {
            "type": "array",
            "description": f"Hasta {MAX_ATTENTION} puntos que requieren atención.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string", "description": "Titular de 6-10 palabras."},
                    "detail": {"type": "string", "description": "Una frase con el porqué."},
                },
                "required": ["title", "detail"],
            },
        },
        "actions": {
            "type": "array",
            "description": f"Hasta {MAX_ACTIONS} acciones concretas.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {"type": "string", "description": "Acción en imperativo."},
                    "owner": {"type": "string", "enum": OWNERS},
                    "due_in_days": {
                        "type": "integer",
                        "description": "Plazo sugerido en días desde hoy.",
                    },
                    "why": {"type": "string", "description": "Para qué, en una frase."},
                },
                "required": ["action", "owner", "due_in_days", "why"],
            },
        },
    },
    "required": ["summary", "key_changes", "attention", "actions"],
}

SYSTEM_PROMPT = f"""Eres el analista financiero de una plataforma de gestión \
para PYMEs peruanas. Recibes métricas YA CALCULADAS del mes y redactas un \
BRIEFING EJECUTIVO para el dueño de la empresa.

Formato del briefing:
- summary: máximo {SUMMARY_WORD_LIMIT} palabras. Es una lectura, no un \
reporte: interpreta el mes en conjunto. NO repitas las cifras que el tablero \
ya muestra (facturación bruta, neta, notas de crédito, entradas, salidas, \
alertas abiertas); explica qué significan juntas.
- key_changes: hasta {MAX_KEY_CHANGES} cambios reales frente al mes anterior.
- attention: hasta {MAX_ATTENTION} puntos que merecen mirada, del más al \
menos importante.
- actions: hasta {MAX_ACTIONS} acciones concretas, cada una con su \
responsable y su plazo en días.

Reglas estrictas:
- Usa exclusivamente los números recibidos; no calcules totales nuevos, no \
sumes monedas distintas, no estimes.
- Escribe los importes en soles como «S/ 12,300». Nunca uses «PEN», «S/.» ni \
códigos de moneda.
- Lenguaje de negocio, sin jerga interna: nada de nombres de campos, códigos \
de hallazgo, siglas de sistemas ni referencias a archivos o bases de datos.
- La facturación emitida es venta facturada, no cobranza. Los comprobantes \
recibidos no son ingresos ni egresos de caja. Los movimientos bancarios \
reportados no son saldo, ingresos ni flujo de caja: el «movimiento bruto» \
suma entradas y salidas, así que no representa nada por sí solo.
- Al comparar movimientos bancarios di siempre si hablas de entradas, de \
salidas o del movimiento bruto.
- Las diferencias entre facturación y movimientos «requieren clasificación o \
revisión contable»; jamás son incumplimiento, evasión, fraude, omisión ni \
ingreso no declarado. Lo que está pendiente de clasificar no es una \
diferencia.
- Español ejecutivo, claro y accionable. Quien lee decide."""


def _money(value: float | int | None) -> str:
    return "—" if value is None else f"S/ {value:,.0f}"


def _pct(value: float | None) -> str:
    return "sin dato" if value is None else f"{value:+.1f}%"


def _flow_lines(data: dict[str, Any], noun: str) -> list[dict[str, Any]]:
    """Últimos meses de un flujo, ya redactados en soles."""
    rows = []
    for row in data["periods"][-4:]:
        pen = row["by_currency"].get("PEN")
        if not pen:
            continue
        rows.append({
            "mes": row["label"],
            f"{noun} bruta": _money(pen["gross"]),
            "notas de crédito": _money(pen["credit_notes"]),
            f"{noun} neta": _money(pen["net"]),
            "variación neta vs mes anterior": _pct(row["variation_pen_pct"]),
            "comprobantes": pen["invoice_count"] + pen["credit_note_count"],
            "anulados": row["cancelled"],
            "rechazados": row["rejected"],
        })
    return rows


def _party_lines(rows: list[dict[str, Any]], window: dict[str, Any] | None) -> list[dict]:
    return [
        {
            "nombre": r["name"],
            "neto en la ventana": _money(r["net_pen"]),
            "participación": f"{r['share_pct']}%",
            "situación": r["status"].replace("_", " "),
            "variación último mes": _pct(r["variation_pct"]),
            "ventana": (window or {}).get("label", ""),
        }
        for r in rows[:5]
    ]


def _itf_lines(itf: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "mes": row["label"],
            "entradas (acreditaciones reportadas)": _money(row["inflow_base"]),
            "variación de entradas": _pct(row["variation_inflow_pct"]),
            "salidas (débitos reportados)": _money(row["outflow_base"]),
            "variación de salidas": _pct(row["variation_outflow_pct"]),
            "impuesto a las transacciones financieras registrado": _money(row["total_tax"]),
            "movimientos sin clasificar": _money(row["unclassified_base"]),
            "movimiento bruto (referencia, suma ambos sentidos)": _money(row["gross_movement"]),
        }
        for row in itf["periods"][-4:]
    ]


def _consistency_lines(consistency: dict[str, Any]) -> dict[str, Any]:
    return {
        "situación": consistency["status"].replace("_", " "),
        "cómo se compara": consistency["methodology"],
        "aclaración": consistency["not_a_breach_note"],
        "diferencias por revisar": [
            {"mes": f["period"], "qué pasa": f["cause"], "detalle": f["explanation"]}
            for f in consistency["findings"]
            if f["classification"] == "requiere_revision"
        ],
        "pendientes de clasificar (no son diferencias)": [
            f["cause"] for f in consistency["findings"]
            if f["classification"] == "informativo"
        ],
    }


def _metrics_payload(period: str) -> dict[str, Any]:
    """Todo lo que ve el modelo, ya en castellano y con importes en soles."""
    docs = load_documents()
    sales = sales_summary(docs, months=6)
    purchases = purchases_summary(docs, months=6)
    customers = customers_analysis(docs)
    suppliers = suppliers_analysis(docs)
    itf = itf_summary(months=6)
    consistency = consistency_analysis(docs)

    return {
        "version del briefing": BRIEFING_VERSION,
        "mes analizado": period,
        "facturación emitida": {
            MEANING_KEY: sales["meaning"],
            "meses": _flow_lines(sales, "facturación"),
        },
        "comprobantes recibidos": {
            MEANING_KEY: purchases["meaning"],
            "meses": _flow_lines(purchases, "compras"),
        },
        "clientes": {
            "principales": _party_lines(customers["parties"], customers["summary"]),
            "concentración": (customers["summary"] or {}).get("concentration_message"),
            "ventana usada": (customers["summary"] or {}).get("window", {}).get("label"),
        },
        "proveedores": {
            "principales": _party_lines(suppliers["parties"], suppliers["summary"]),
            "aumentos inusuales": (suppliers["summary"] or {}).get("unusual_increases", []),
        },
        "movimientos bancarios reportados": {
            MEANING_KEY: itf["meaning"],
            "sobre el movimiento bruto": itf["gross_movement_note"],
            "meses": _itf_lines(itf),
        },
        "cruce entre facturación y movimientos": _consistency_lines(consistency),
    }


def _fingerprint(metrics: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(metrics, sort_keys=True, default=str).encode()
    ).hexdigest()


# ── Limpieza y recorte de lo que devuelve el modelo ──

# Restos de jerga que no deben llegar al usuario, por si el modelo los
# reintroduce pese al payload humanizado.
_JARGON = [
    (re.compile(r"\bPEN\b\s?"), "S/ "),
    (re.compile(r"S/\.\s*"), "S/ "),
    (re.compile(r"\bSoles\b"), "soles"),
    (re.compile(r"\bCPE\b"), "comprobantes electrónicos"),
    (re.compile(r"\bXML\b\s?"), ""),
    (re.compile(r"\bRUC\s+\d{11}\b"), "el contribuyente"),
    # Identificadores tipo net_pen / itf_outflow_without_cpe.
    (re.compile(r"\b[a-z]{2,}(?:_[a-z]{2,})+\b"), ""),
]


def clean_text(text: str) -> str:
    out = (text or "").strip()
    for pattern, replacement in _JARGON:
        out = pattern.sub(replacement, out)
    return re.sub(r"\s{2,}", " ", out).strip(" ·-,")


def _cap_words(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit]).rstrip(".,;:") + "…"


def action_id(period: str, text: str) -> str:
    """Identificador estable de una acción: sobrevive a las regeneraciones y
    con él viaja el estado que puso la persona."""
    return hashlib.sha1(f"{period}|{text.lower()}".encode()).hexdigest()[:12]


def _build_actions(
    raw: list[dict[str, Any]], period: str, today: datetime.date,
    previous: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    kept_status = {a.get("id"): a.get("status") for a in previous}
    actions = []
    for item in raw[:MAX_ACTIONS]:
        text = clean_text(item.get("action", ""))
        if not text:
            continue
        days = item.get("due_in_days")
        if not isinstance(days, int):
            days = MIN_DUE_DAYS
        days = max(MIN_DUE_DAYS, min(MAX_DUE_DAYS, days))
        identifier = action_id(period, text)
        owner = item.get("owner")
        actions.append({
            "id": identifier,
            "action": text,
            "owner": owner if owner in OWNERS else OWNERS[0],
            "due_date": (today + datetime.timedelta(days=days)).isoformat(),
            # El estado es de la persona: si ya lo movió, se respeta.
            "status": kept_status.get(identifier) or ActionStatus.SUGGESTED,
            "why": clean_text(item.get("why", "")),
        })
    return actions


def _shape(result: dict[str, Any], period: str, previous: list[dict[str, Any]]
           ) -> dict[str, Any]:
    today = timezone.localdate()
    attention = []
    for item in result.get("attention", [])[:MAX_ATTENTION]:
        title = clean_text(item.get("title", ""))
        if title:
            attention.append({"title": title, "detail": clean_text(item.get("detail", ""))})
    return {
        "summary": _cap_words(clean_text(result.get("summary", "")), SUMMARY_WORD_LIMIT),
        "key_changes": [
            clean_text(c) for c in result.get("key_changes", [])[:MAX_KEY_CHANGES]
            if clean_text(c)
        ],
        "attention": attention,
        "actions": _build_actions(result.get("actions", []), period, today, previous),
    }


# ── Fuentes del briefing (deterministas, nunca redactadas por el modelo) ──

def briefing_sources(docs, itf: dict[str, Any], open_alerts: int) -> list[dict[str, str]]:
    """De dónde sale cada número, para la sección de fuentes."""
    issued = sum(1 for d in docs if d.direction == "emitida")
    periods = sorted({d.period for d in docs if d.period})
    itf_periods = [p["period"] for p in itf["periods"]]
    itf_records = sum(p["records"] for p in itf["periods"])
    return [
        {
            "label": "Comprobantes electrónicos",
            "detail": (
                f"{len(docs):,} comprobantes sincronizados ({issued:,} emitidos, "
                f"{len(docs) - issued:,} recibidos)"
                + (f" · periodos {periods[0]} a {periods[-1]}" if periods else "")
            ),
        },
        {
            "label": "Movimientos bancarios reportados",
            "detail": (
                f"{itf_records:,} registros en {len(itf_periods)} mes(es)"
                + (f" · entidades: {', '.join(itf['banks'])}" if itf["banks"] else "")
            ) if itf_periods else "Sin movimientos sincronizados",
        },
        {
            "label": "Alertas financieras",
            "detail": f"{open_alerts} abierta(s) al momento de generar el briefing",
        },
    ]


def payload(row: FinanceAiSummary) -> dict[str, Any]:
    return {
        "period": row.period,
        "summary": row.summary,
        "key_changes": row.key_changes,
        "attention": row.attention,
        "actions": row.actions,
        "generated_at": row.updated_at,
    }


def latest_summary(period: str | None = None) -> FinanceAiSummary | None:
    """El último briefing disponible; el del periodo si se indica."""
    rows = FinanceAiSummary.objects.filter(account_ruc=settings.SUNAT_RUC)
    if period:
        rows = rows.filter(period=period)
    return rows.order_by("-period", "-created_at").first()


def has_briefing(row: FinanceAiSummary | None) -> bool:
    """Un resumen guardado en el formato anterior conserva el texto pero no
    los tres bloques. Sin bloques no hay briefing que mostrar: la vista
    invita a generarlo en lugar de pintar tres columnas vacías."""
    return bool(row and (row.key_changes or row.attention or row.actions))


def get_or_create_summary(period: str, force: bool = False) -> FinanceAiSummary:
    ruc = settings.SUNAT_RUC
    metrics = _metrics_payload(period)
    fingerprint = _fingerprint(metrics)

    existing = latest_summary(period)
    if existing and existing.fingerprint == fingerprint and not force:
        return existing

    result = llm.structured_completion(
        SYSTEM_PROMPT,
        json.dumps(metrics, ensure_ascii=False, default=str),
        "finance_briefing",
        SUMMARY_SCHEMA,
    )
    shaped = _shape(result, period, existing.actions if existing else [])

    if existing:
        existing.fingerprint = fingerprint
        existing.model_name = llm.INTEL_MODEL
        for field, value in shaped.items():
            setattr(existing, field, value)
        existing.save()
        return existing
    return FinanceAiSummary.objects.create(
        account_ruc=ruc,
        period=period,
        fingerprint=fingerprint,
        model_name=llm.INTEL_MODEL,
        **shaped,
    )
