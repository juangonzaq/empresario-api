"""Endpoints and listing definitions for SUNAFIL's casilla electrónica."""

from __future__ import annotations

from dataclasses import dataclass

BASE_URL = "https://casillaelectronica.sunafil.gob.pe"
ENTRY_PATH = "/si.inbox/Login/SUNAT"
LANDING_PATH = "/si.inbox/Inicio/Empleador"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 90

# Employer access is delegated to SUNAT Clave SOL; the client id and redirect are
# read from the entry page rather than hardcoded, since SUNAFIL rotates them.
SUNAT_AUTH_BASE = "https://api-seguridad.sunat.gob.pe"

VIEW_STATE_FIELD = "javax.faces.ViewState"

# The session-expired modal is present on every page; ignore it when parsing.
SESSION_EXPIRED_MARKER = "La sesión ha caducado"


@dataclass(frozen=True)
class ListingSpec:
    """One of the casilla's listing screens."""

    kind: str
    label: str
    path: str
    form_id: str
    table_id: str
    # Opening a detail marks the item as read on SUNAFIL's side. For requirements
    # and notifications that acknowledgement carries legal deadlines, so only the
    # orientation listing is safe to open automatically.
    detail_is_safe: bool = False
    detail_path: str = ""
    # The detail page renders its own form, whose id differs from the listing's.
    detail_form_id: str = ""


ORIENTATION = "orientation"
REQUIREMENT = "requirement"
INSPECTION_NOTICE = "inspection_notice"
COLLECTION_NOTICE = "collection_notice"

LISTINGS: tuple[ListingSpec, ...] = (
    ListingSpec(
        kind=ORIENTATION,
        label="Sunafil te Orienta",
        path="/si.inbox/Orientacion/ListadoOrientacionEmpleador",
        form_id="formOrientacionEmpleador",
        table_id="formOrientacionEmpleador:dtAlertas",
        detail_is_safe=True,
        detail_path="/si.inbox/Orientacion/DetallePlantillaOrientacionEmpleador",
        detail_form_id="formDetallePlantillaOrientacionEmpleador",
    ),
    ListingSpec(
        kind=REQUIREMENT,
        label="Acciones previas / requerimientos",
        path="/si.inbox/ActosAdministrativos/ListadoRequerimientos",
        form_id="formactosAdministrativos",
        table_id="formactosAdministrativos:dtAlertas",
    ),
    ListingSpec(
        kind=INSPECTION_NOTICE,
        label="Notificaciones de fiscalización",
        path="/si.inbox/Notificacion/Empleador",
        form_id="formNotificacion",
        table_id="formNotificacion:dtNotificaciones",
    ),
    ListingSpec(
        kind=COLLECTION_NOTICE,
        label="Notificaciones de cobranza",
        path="/si.inbox/Notificacion/CobroOrdinario",
        form_id="formNotificacion",
        table_id="formNotificacion:dtNotificaciones",
    ),
)

LISTINGS_BY_KIND = {spec.kind: spec for spec in LISTINGS}

# Column headers, per listing, that identify a row across runs.
IDENTITY_COLUMNS = {
    ORIENTATION: ("Fecha de Depósito", "Asunto"),
    REQUIREMENT: ("Registro",),
    INSPECTION_NOTICE: ("Orden de Inspección",),
    COLLECTION_NOTICE: ("Expediente Sancionador",),
}

READ_STATUS = "LEÍDO"
