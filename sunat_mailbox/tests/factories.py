"""Test helpers that build SUNAT-shaped payloads and model instances."""

from __future__ import annotations

from typing import Any

from sunat_mailbox.models import Message, MessageType

TAXPAYER_ID = "20604442533"


def list_row(**overrides: Any) -> dict[str, Any]:
    """A row as returned by ``listNotiMenPag``."""
    row = {
        "codMensaje": 1043884152,
        "indEstado": 1,
        "indDesta": 0,
        "indUrg": 0,
        "indTipmsj": 2,
        "desAsunto": "Notificaci&oacute;n de Resoluci&oacute;n de Multa",
        "fecEnvio": "22/07/2026",
        "fecPublica": "22/07/2026 16:17:43",
        "fecVigencia": "2126-07-22 15:44:26.0",
        "codDepen": "0023",
        "cantidadArchAdj": 1,
        "codEtiqueta": "10",
    }
    return {**row, **overrides}


def detail_payload(**overrides: Any) -> dict[str, Any]:
    """A payload as returned by ``obtenerDetalleNotiMen``."""
    payload = {
        "nombUsuario": "PATTERN GROUP S.A.C.",
        "fecLectura": None,
        "listAttach": [
            # SUNAT emits placeholder rows with no file; they must be skipped.
            {"codArchivo": None, "nomArchivo": None, "numId": 200132},
            {
                "codArchivo": 1112590410,
                "nomArchivo": "constancia_20260722154537.pdf",
                "nomAdjunto": "Constancia",
                "cntTamarch": 51200,
                "tamanoArchivoFormat": "50 KB",
                "numId": 200133,
            },
        ],
    }
    return {**payload, **overrides}


def create_message(**overrides: Any) -> Message:
    defaults = {
        "taxpayer_id": TAXPAYER_ID,
        "message_code": 1043884152,
        "message_type": MessageType.NOTIFICATION,
        "subject": "Notification of penalty resolution",
        "sent_on": "2026-07-22",
        "published_at": "2026-07-22T16:17:43-05:00",
        "attachment_count": 1,
    }
    return Message.objects.create(**{**defaults, **overrides})
