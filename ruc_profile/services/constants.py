"""Catalogue of the sections behind the buttons on SUNAT's RUC consultation page.

Each button posts back to the same URL with a different ``accion``, carrying the RUC
and the business name. The responses are plain HTML, shaped either as data tables or
as a single yes/no answer.
"""

from __future__ import annotations

from dataclasses import dataclass

# Every button posts these alongside accion/nroRuc/desRuc.
FORM_CONTEXT = "ti-it"
FORM_MODE = "1"

KIND_TABLE = "table"
KIND_BOOLEAN = "boolean"


@dataclass(frozen=True)
class SectionSpec:
    """One button on the consultation page."""

    key: str      # stable slug used in the database and the API
    action: str   # SUNAT's ``accion`` parameter
    label: str
    kind: str = KIND_TABLE
    # True when finding data means something is wrong with the taxpayer.
    is_risk_signal: bool = False


SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec("historical", "getinfHis", "Información histórica"),
    SectionSpec("coactive_debt", "getInfoDC", "Deuda coactiva", is_risk_signal=True),
    SectionSpec("tax_omissions", "getInfoOT", "Omisiones tributarias", is_risk_signal=True),
    SectionSpec("workers", "getCantTrab", "Cantidad de trabajadores"),
    SectionSpec("probatory_acts", "getActPro", "Actas probatorias", is_risk_signal=True),
    SectionSpec("physical_invoices", "getActCPF", "Facturas físicas"),
    SectionSpec("reactiva_peru", "getReactivaPeru", "Reactiva Perú: deuda coactiva",
                kind=KIND_BOOLEAN, is_risk_signal=True),
    SectionSpec("covid_guarantee", "getPGarantiaCOVID19",
                "Garantías COVID-19: deuda coactiva",
                kind=KIND_BOOLEAN, is_risk_signal=True),
    SectionSpec("legal_representatives", "getRepLeg", "Representantes legales"),
)

SECTIONS_BY_KEY = {spec.key: spec for spec in SECTIONS}

# Sections whose rows are also stored as first-class models.
SECTION_WORKERS = "workers"
SECTION_LEGAL_REPRESENTATIVES = "legal_representatives"

# SUNAT signals "nothing to show" with prose rather than an empty table.
NO_DATA_MARKERS = (
    "no hay informaci",
    "no existe informaci",
    "no se ha remitido",
    "no se tiene",
    "no registra",
    "sin informaci",
)
