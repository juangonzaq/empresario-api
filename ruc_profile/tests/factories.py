"""HTML fixtures mirroring the section pages behind each button."""

from __future__ import annotations

RUC = "20604442533"
NAME = "PATTERN GROUP S.A.C."

PAGE = """
<html><body>
  <h4>{title}</h4>
  <div class="list-group">{body}</div>
</body></html>
"""


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows
    )
    return f"<table><tr>{head}</tr>{body}</table>"


def table_page(headers: list[str], rows: list[list[str]], title: str = "SECCIÓN") -> str:
    return PAGE.format(title=title, body=_table(headers, rows))


def workers_page(rows: list[list[str]] | None = None) -> str:
    return table_page(
        ["Período", "N° de Trabajadores", "N° de Pensionistas",
         "N° de Prestadores de Servicio"],
        rows if rows is not None else [
            ["2025-06", "7", "0", "2"],
            ["2025-07", "7", "0", "2"],
            ["2026-05", "5", "1", "3"],
        ],
        title=f"CANTIDAD DE TRABAJADORES DE {RUC} - {NAME}",
    )


def representatives_page() -> str:
    return table_page(
        ["Documento", "Nro. Documento", "Nombre", "Cargo", "Fecha Desde"],
        [["DNI", "70030212", "GONZALES QUISPE JUAN CARLOS", "GERENTE GENERAL",
          "22/03/2019"]],
        title=f"REPRESENTANTES LEGALES DE {RUC} - {NAME}",
    )


def empty_table_page() -> str:
    """SUNAT fills an empty result with a single prose row."""
    return table_page(
        ["Nº Acta Probatoria", "Fecha de Acta Probatoria"],
        [["No existe información registrada para el contribuyente consultado"]],
        title="ACTAS PROBATORIAS",
    )


def no_data_text_page() -> str:
    """Sections with nothing to show sometimes carry no table at all."""
    return PAGE.format(
        title="DEUDA COACTIVA",
        body="<div class='list-group-item'>No se ha remitido deuda en cobranza "
             "coactiva que corresponda al contribuyente consultado.</div>",
    )


def coactive_debt_page() -> str:
    return table_page(
        ["Nº", "Monto", "Periodo Tributario", "Fecha Inicio Cobranza", "Entidad"],
        [["1", "12500.00", "202401", "15/03/2025", "SUNAT"]],
        title="DEUDA COACTIVA",
    )


def boolean_page(answer: str = "NO") -> str:
    # Double-encoded entities, exactly as SUNAT serves them.
    return PAGE.format(
        title=f"REACTIVA PER&Uacute; DE {RUC} - {NAME}",
        body="<div class='row'><div class='col-md-12'>Resultado de la B&uacute;squeda "
             "&#191;Tiene deuda en cobranza coactiva mayor a una (1) UIT&#63;</div>"
             f"<div class='col-md-12'>{answer}</div></div>",
    )


def historical_page() -> str:
    """Mixes a table with data and one that says there is none."""
    return PAGE.format(
        title=f"INFORMACION HISTORICA DE {RUC} - {NAME}",
        body=(
            _table(["Nombre o Razón Social:", "Fecha de Baja:"],
                   [["No hay Información", "-"]])
            + _table(["Condición del Contribuyente", "Fecha Desde", "Fecha Hasta"],
                     [["HABIDO", "16/04/2019", "10/10/2024"]])
        ),
    )
