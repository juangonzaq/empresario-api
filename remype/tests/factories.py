"""Fixtures mimicking REMYPE responses."""

from __future__ import annotations

import json
from typing import Any

RUC_REGISTERED = "20604442533"
RUC_UNREGISTERED = "20100070970"

REGISTERED_ROW = {
    "RAZON_SOCIAL": "PATTERN GROUP S.A.C.",
    "NUMEROFICHASOLICITUD": "0002117244-2023",
    "NUMERORUC": RUC_REGISTERED,
    "FECHASOLICITUD": "14/06/2023",
    "FECHAACREDITACION": "16/06/2023",
    "N_CODREG": 678874,
    "FLG_MYPE": "ACREDITADO COMO MICRO EMPRESA",
    # REMYPE pads its strings and uses a dashed placeholder for absent dates.
    "SITUACIONEMPRESA": "   ACREDITADO   ",
    "CONDICION": "ACREDITADO COMO MICRO EMPRESA",
    "FECHABAJA": "   --- --- ---   ",
    "FECHARLE": "   --- --- ---   ",
    "OFICIO": "   --- --- ---   ",
}


def found_response(**overrides: Any) -> dict[str, Any]:
    row = {**REGISTERED_ROW, **overrides}
    return {"status": 200, "body": json.dumps({"status": "0", "data": [row], "message": None})}


def not_found_response() -> dict[str, Any]:
    return {
        "status": 200,
        "body": json.dumps({
            "status": "1", "data": None,
            "message": "No se tiene información del RUC ingresado en REMYPE.",
        }),
    }


def captcha_rejected_response() -> dict[str, Any]:
    return {
        "status": 401,
        "body": json.dumps({"status": "401", "message": "Captcha invalido"}),
    }
