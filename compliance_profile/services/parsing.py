"""Turns SUNAT compliance payloads into model field values."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from django.utils import timezone

# Maps the detalle payload's top-level keys onto VariableType codes, used when a
# section omits codTipoVariable.
DETAIL_SECTIONS = {
    "varPonderacion": "P",
    "varVinculacion": "V",
    "varCalificacionDirecta": "D",
}


def parse_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_datetime(value: Any) -> datetime | None:
    """Parse SUNAT's ISO timestamps (``2026-07-04T00:00:00.000-05:00``)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def header_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Model field values for one ``cabecera`` object (vigente or histórico)."""
    return {
        "execution_period": parse_int(row.get("perEjec")),
        "preliminary_category": (row.get("codCatPrel") or "").strip(),
        "rating": (row.get("codVarFinal") or "").strip(),
        "evaluation_start": parse_int(row.get("perEvalIni")),
        "evaluation_end": parse_int(row.get("perEvalFin")),
        "data_location_code": parse_int(row.get("codUbicaDatos")),
        "loaded_at": parse_datetime(row.get("fecCarga")),
        "header_payload": row,
    }


def iter_detail_variables(detail: dict[str, Any] | None) -> Iterator[dict[str, Any]]:
    """Yield ComplianceVariable field values for every variable in a detalle.

    Sections a taxpayer was not affected by come back as ``null`` and are skipped.
    """
    for key, fallback_type in DETAIL_SECTIONS.items():
        section = (detail or {}).get(key)
        if not section:
            continue
        variable_type = (section.get("codTipoVariable") or fallback_type).strip()
        type_label = section.get("desTipoVariable") or ""
        for variable in section.get("lisVars") or []:
            records = variable.get("lisCampos") or []
            yield {
                "variable_type": variable_type,
                "type_label": type_label,
                "code": variable.get("codVariable") or "",
                "description": variable.get("desVariable") or "",
                "severity": variable.get("desGravedad") or "",
                "entity_name": variable.get("nomEntidad") or "",
                "is_complete": bool(variable.get("indCompletado")),
                "is_multipage": bool(variable.get("indMultipagina")),
                "field_metadata": variable.get("metadataCampos") or {},
                "records": records,
                "record_count": len(records),
                "observation": variable.get("observacion"),
            }
