"""Trimmed real payloads from SUNAT's perfilcumplimiento endpoints."""

TAXPAYER_ID = "20604442533"

CURRENT_HEADER = {
    "numRuc": TAXPAYER_ID,
    "perEjec": 202605,
    "trimCal": 202602,
    "codCatPrel": "D",
    "codVarFinal": "D",
    "perEvalIni": 202507,
    "perEvalFin": 202606,
    "codUbicaDatos": 2,
    "fecCarga": "2026-07-04T00:00:00.000-05:00",
}

HISTORY = {
    "cabecera": [
        {
            "numRuc": TAXPAYER_ID,
            "perEjec": 202602,
            "trimCal": 202601,
            "codCatPrel": "D",
            "codVarFinal": "D",
            "perEvalIni": 202504,
            "perEvalFin": 202603,
            "codUbicaDatos": 2,
            "fecCarga": "2026-04-08T00:00:00.000-05:00",
        },
        {
            "numRuc": TAXPAYER_ID,
            "perEjec": 202508,
            "trimCal": 202503,
            "codCatPrel": "C",
            "codVarFinal": "C",
            "perEvalIni": 202410,
            "perEvalFin": 202509,
            "codUbicaDatos": 2,
            "fecCarga": "2025-10-06T00:00:00.000-05:00",
        },
    ]
}

DETAIL = {
    "varPonderacion": {
        "codTipoVariable": "P",
        "desTipoVariable": "Ponderación",
        "lisVars": [
            {
                "codVariable": "v0615",
                "desVariable": (
                    "No ha efectuado el pago del íntegro de las obligaciones "
                    "tributarias por el IGV al vencimiento de dichas obligaciones "
                    "hasta en tres (3) o más periodos mensuales."
                ),
                "metadataCampos": {
                    "PER_DECLA": {"numCampo": 1, "desCampo": "Periodo Mensual Declarado"},
                    "COD_TRIBUTO": {"numCampo": 2, "desCampo": "Código de Tributo"},
                },
                "lisCampos": [
                    {
                        "PER_DECLA": {"valCampo": "202504", "desValor": "202504"},
                        "COD_TRIBUTO": {"valCampo": "010101", "desValor": "010101"},
                    },
                    {
                        "PER_DECLA": {"valCampo": "202505", "desValor": "202505"},
                        "COD_TRIBUTO": {"valCampo": "010101", "desValor": "010101"},
                    },
                ],
                "desGravedad": "Muy grave",
                "nomEntidad": "T12021DETPEIGVOTI",
                "indCompletado": True,
                "indMultipagina": False,
                "observacion": {
                    "codEstado": "01", "desEstado": "Pendiente",
                    "codNegocio": None, "desNegocio": None,
                    "desSustento": None, "auditoria": None,
                },
            },
        ],
    },
    "varCalificacionDirecta": None,
    "varVinculacion": None,
}
