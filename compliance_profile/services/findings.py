"""Normalises stored SUNAT compliance variables into UI-ready findings.

The models keep SUNAT's payloads verbatim (audit trail); this module is the
read layer that groups variables by ``code``, normalises severities, statuses,
periods, dates and amounts, and builds the summary / list / detail payloads.
It never sums amounts into a "current debt" figure and never invents trends.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from ..models import ComplianceRating

# ── Configurable catalog ────────────────────────────────────────────────────

SUNAT_FINDING_CATALOG: dict[str, dict[str, Any]] = {
    "v0615": {
        "title": "Pagos de IGV fuera de plazo",
        "category": "tax_payment",
        "display_order": 1,
    },
    "v0616": {
        "title": "Pagos de renta fuera de plazo",
        "category": "tax_payment",
        "display_order": 2,
    },
    "v0618": {
        "title": "Aportes EsSalud fuera de plazo",
        "category": "payroll_contribution",
        "display_order": 3,
    },
    "v0619": {
        "title": "Aportes ONP fuera de plazo",
        "category": "payroll_contribution",
        "display_order": 4,
    },
    "v0831": {
        "title": "Deuda en cobranza coactiva",
        "category": "tax_debt",
        "display_order": 5,
    },
    "v1313": {
        "title": "Resolución de multa tributaria",
        "category": "tax_penalty",
        "display_order": 6,
    },
}

SEVERITY_MAPPING: dict[str, dict[str, Any]] = {
    "Muy grave": {"code": "very_serious", "priority": 4},
    "Grave": {"code": "serious", "priority": 3},
    "Grave reconocida": {"code": "serious", "priority": 3},
    "Leve": {"code": "minor", "priority": 2},
    "Informativa": {"code": "informational", "priority": 1},
}

STATUS_MAPPING: dict[str, str] = {
    "Pendiente": "pending",
    "En descargo": "under_appeal",
    "Reconocida": "acknowledged",
    "Subsanada": "remediated",
}

# Presentation only — never alters SUNAT's category.
RATING_UI_STATUS: dict[str, str] = {
    "A": "controlled",
    "B": "controlled",
    "C": "attention",
    "D": "requires_attention",
    "E": "critical",
}

# ── Record normalisation ────────────────────────────────────────────────────

PERIOD_FIELDS = (
    "COD_PERDECLA",
    "COD_PERPAG",
    "COD_PERTRIB",
    "COD_PERMEN",
    "PER_DECLA",
)

FIELD_MAPPING = {
    "FEC_VEN": "due_date",
    "FEC_PAGO": "payment_date",
    "FEC_PREDECLA": "declaration_date",
    "FEC_NOTIF": "notification_date",
    "FEC_EMI": "issue_date",
    "MTO_PAGO": "paid_amount",
    "MTO_DAPP": "declared_debt_amount",
    "MTO_VALOR": "assessed_amount",
    "COD_TRIBUTO": "tax_code",
    "COD_FORMULARIO": "form_code",
    "NUM_ORDEN": "order_number",
    "NUM_ABONO": "installment_number",
    "NUM_VALOR": "document_number",
}

DATE_FIELDS = {"due_date", "payment_date", "declaration_date", "notification_date", "issue_date"}
AMOUNT_FIELDS = {"paid_amount", "declared_debt_amount", "assessed_amount"}


def get_record_value(record: dict[str, Any], field: str) -> str | None:
    value = record.get(field)
    if not value:
        return None
    return value.get("valCampo") or value.get("desValor")


def get_period(record: dict[str, Any]) -> str | None:
    for field in PERIOD_FIELDS:
        value = get_record_value(record, field)
        if value and len(value) >= 6:
            return f"{value[:4]}-{value[4:6]}"
    return None


def parse_record_date(value: str | None) -> date | None:
    """SUNAT detail records use ``dd/mm/yyyy``."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def parse_amount(value: str | None) -> float | int | None:
    if not value:
        return None
    try:
        number = float(value.replace(",", ""))
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def calculate_delay_days(due_date: date | None, payment_date: date | None) -> int | None:
    if not due_date or not payment_date:
        return None
    return max((payment_date - due_date).days, 0)


def detect_event_type(record: dict[str, Any]) -> str:
    if "FEC_VEN" in record and "FEC_PAGO" in record:
        return "late_payment"
    if "MTO_DAPP" in record:
        return "declared_debt"
    if "NUM_ABONO" in record:
        return "payment_installment"
    return "sunat_observation"


def format_period(value: int | None) -> str | None:
    """``202507`` (int) → ``"2025-07"``."""
    if not value:
        return None
    text = str(value)
    if len(text) < 6:
        return text
    return f"{text[:4]}-{text[4:6]}"


def normalize_event(record: dict[str, Any], *, code: str, entity_name: str) -> dict[str, Any]:
    """One SUNAT record → one normalised event. Records are never merged."""
    event: dict[str, Any] = {
        "event_type": detect_event_type(record),
        "period": get_period(record),
        "currency": "PEN",
    }
    for sunat_field, name in FIELD_MAPPING.items():
        raw = get_record_value(record, sunat_field)
        if name in DATE_FIELDS:
            parsed_date = parse_record_date(raw)
            event[name] = parsed_date.isoformat() if parsed_date else None
        elif name in AMOUNT_FIELDS:
            event[name] = parse_amount(raw)
        else:
            event[name] = raw
    event["delay_days"] = calculate_delay_days(
        parse_record_date(get_record_value(record, "FEC_VEN")),
        parse_record_date(get_record_value(record, "FEC_PAGO")),
    )
    # Exact-duplicate key only: same code + entity + period + order + type.
    dedup_key = "|".join(
        str(part)
        for part in (
            code, entity_name, event["period"], event["order_number"], event["event_type"]
        )
    )
    event["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"sunat-finding-event:{dedup_key}"))
    event["_dedup_key"] = dedup_key
    return event


# ── Grouping ────────────────────────────────────────────────────────────────


def _severity_entry(label: str) -> dict[str, Any]:
    return SEVERITY_MAPPING.get(label, {"code": "unknown", "priority": 0})


def _status_from_observation(observation: dict[str, Any] | None) -> tuple[str, str]:
    """Normalised (status, status_label) from SUNAT's descargo state."""
    label = ((observation or {}).get("desEstado") or "Pendiente").strip()
    return STATUS_MAPPING.get(label, "pending"), label


def _new_finding(code: str, description: str) -> dict[str, Any]:
    catalog = SUNAT_FINDING_CATALOG.get(code, {})
    return {
        "code": code,
        "title": catalog.get("title", f"Hallazgo SUNAT {code}"),
        "category": catalog.get("category", "other"),
        "display_order": catalog.get("display_order", 99),
        "official_description": description,
        "severity": "unknown",
        "severity_label": "",
        "_priority": 0,
        "status": "pending",
        "status_label": "Pendiente",
        "_statuses": [],
        "events": [],
        "_seen": set(),
        "sources": [],
        "is_complete": True,
        "is_multipage": False,
    }


def _merge_variable(finding: dict[str, Any], variable: Any) -> None:
    """Fold one ComplianceVariable row into its grouped finding."""
    if variable.description and not finding["official_description"]:
        finding["official_description"] = variable.description
    entry = _severity_entry(variable.severity)
    if entry["priority"] > finding["_priority"]:
        finding["_priority"] = entry["priority"]
        finding["severity"] = entry["code"]
        finding["severity_label"] = variable.severity
    finding["_statuses"].append(_status_from_observation(variable.observation))
    if variable.entity_name and variable.entity_name not in finding["sources"]:
        finding["sources"].append(variable.entity_name)
    finding["is_complete"] = finding["is_complete"] and variable.is_complete
    finding["is_multipage"] = finding["is_multipage"] or variable.is_multipage
    for record in variable.records or []:
        event = normalize_event(record, code=finding["code"], entity_name=variable.entity_name)
        if event["_dedup_key"] not in finding["_seen"]:
            finding["_seen"].add(event["_dedup_key"])
            finding["events"].append(event)


def _finalize_finding(finding: dict[str, Any]) -> dict[str, Any]:
    statuses = finding.pop("_statuses")
    # Pending wins: a finding is only non-pending when every variable is.
    non_pending = [s for s in statuses if s[0] != "pending"]
    if statuses and len(non_pending) == len(statuses):
        finding["status"], finding["status_label"] = non_pending[0]
    finding.pop("_seen")
    for event in finding["events"]:
        event.pop("_dedup_key", None)
    finding["events"].sort(key=lambda e: (e["period"] or "", e["event_type"]), reverse=True)
    periods = sorted({e["period"] for e in finding["events"] if e["period"]})
    finding["affected_periods"] = periods
    finding["_latest_period"] = periods[-1] if periods else ""
    finding["event_count"] = len(finding["events"])
    finding["has_detail"] = finding["event_count"] > 0
    return finding


def build_findings(rating: ComplianceRating) -> list[dict[str, Any]]:
    """Group the rating's variables by ``code`` into normalised findings."""
    grouped: dict[str, dict[str, Any]] = {}
    for variable in rating.variables.all():
        code = variable.code or "sin-codigo"
        if code not in grouped:
            grouped[code] = _new_finding(code, variable.description)
        _merge_variable(grouped[code], variable)

    findings = [_finalize_finding(finding) for finding in grouped.values()]

    # Stable multi-pass sort; the last pass is the primary key:
    # severity DESC → latest period DESC → pending first → catalog order.
    findings.sort(key=lambda f: f["display_order"])
    findings.sort(key=lambda f: 0 if f["status"] == "pending" else 1)
    findings.sort(key=lambda f: f["_latest_period"], reverse=True)
    findings.sort(key=lambda f: f["_priority"], reverse=True)
    return findings


def _strip_internal(finding: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in finding.items() if not k.startswith("_")}


# ── Payload builders ────────────────────────────────────────────────────────


def classification_type(rating: ComplianceRating) -> str:
    """"preliminary" while SUNAT is still inside the evaluation window."""
    if not rating.rating:
        return "preliminary"
    if (
        rating.execution_period
        and rating.evaluation_end
        and rating.execution_period <= rating.evaluation_end
    ):
        return "preliminary"
    return "final"


def build_trend(rating: ComplianceRating, previous: ComplianceRating | None) -> dict[str, Any]:
    """Trend vs the previous stored evaluation. Never invented without history."""
    if previous is None or not previous.rating or not rating.rating:
        return {"previous_rating": None, "direction": None, "label": None}
    current_value, previous_value = rating.rating, previous.rating
    if current_value > previous_value:  # A best … E worst
        direction, label = "down", f"Bajó de {previous_value} a {current_value}"
    elif current_value < previous_value:
        direction, label = "up", f"Subió de {previous_value} a {current_value}"
    else:
        direction, label = "same", f"Se mantiene en {current_value}"
    return {
        "previous_rating": previous_value,
        "current_rating": current_value,
        "direction": direction,
        "label": label,
    }


def _severity_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"very_serious": 0, "serious": 0, "minor": 0, "informational": 0}
    for finding in findings:
        if finding["severity"] in counts:
            counts[finding["severity"]] += 1
    return counts


def build_summary(
    rating: ComplianceRating, previous: ComplianceRating | None
) -> dict[str, Any]:
    findings = build_findings(rating)
    severity_counts = _severity_counts(findings)
    return {
        "source": "SUNAT",
        "taxpayer_id": rating.taxpayer_id,
        "rating": rating.rating or None,
        "rating_label": rating.get_rating_display() if rating.rating else None,
        "classification_type": classification_type(rating),
        "ui_status": RATING_UI_STATUS.get(rating.rating, "unknown"),
        "evaluation": {
            "start": format_period(rating.evaluation_start),
            "end": format_period(rating.evaluation_end),
            "execution_period": format_period(rating.execution_period),
        },
        "counts": {
            "unique_findings": len(findings),
            **{k: v for k, v in severity_counts.items() if v},
            "pending": sum(1 for f in findings if f["status"] == "pending"),
        },
        "top_findings": [
            {
                "code": f["code"],
                "title": f["title"],
                "severity": f["severity"],
                "affected_records": f["event_count"],
            }
            for f in findings[:3]
        ],
        "trend": build_trend(rating, previous),
        "data_quality": {
            "has_detail": rating.detail_fetched_at is not None,
            "is_complete": all(f["is_complete"] for f in findings) if findings else True,
            "last_updated_at": rating.detail_fetched_at,
        },
    }


def build_findings_list(rating: ComplianceRating) -> dict[str, Any]:
    findings = build_findings(rating)
    severity_counts = _severity_counts(findings)
    return {
        "rating": {
            "value": rating.rating or None,
            "label": rating.get_rating_display() if rating.rating else None,
            "classification_type": classification_type(rating),
        },
        "evaluation": {
            "start": format_period(rating.evaluation_start),
            "end": format_period(rating.evaluation_end),
        },
        "summary": {
            "unique_findings": len(findings),
            **{k: v for k, v in severity_counts.items() if v},
        },
        "findings": [
            {
                "code": f["code"],
                "title": f["title"],
                "category": f["category"],
                "official_description": f["official_description"],
                "severity": f["severity"],
                "severity_label": f["severity_label"],
                "status": f["status"],
                "status_label": f["status_label"],
                "affected_periods": f["affected_periods"],
                "event_count": f["event_count"],
                "has_detail": f["has_detail"],
            }
            for f in findings
        ],
        "updated_at": rating.detail_fetched_at,
    }


def build_finding_detail(rating: ComplianceRating, code: str) -> dict[str, Any] | None:
    findings = build_findings(rating)
    finding = next((f for f in findings if f["code"] == code), None)
    if finding is None:
        return None
    payload = _strip_internal(finding)
    payload["data_quality"] = {
        "is_complete": payload.pop("is_complete"),
        "is_multipage": payload.pop("is_multipage"),
        "fetched_at": rating.detail_fetched_at,
    }
    payload.pop("display_order", None)
    return payload
