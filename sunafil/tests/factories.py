"""HTML fixtures mirroring the casilla's JSF pages."""

from __future__ import annotations

RUC = "20604442533"

# Every casilla page embeds this hidden modal; parsing must ignore it.
SESSION_MODAL = """
<table><tr><th>La sesión ha caducado, por favor, pulse sobre el siguiente botón
para volver a acceder a la aplicación.</th></tr>
<tr><td>Volver a acceder</td></tr></table>
"""

LISTING = """
<html><body>
<form id="{form_id}" action="{path}" method="post">
  <table id="{table_id}">
    <tr>{headers}</tr>
    {rows}
  </table>
  <input type="hidden" name="javax.faces.ViewState" value="{view_state}"/>
</form>
{modal}
</body></html>
"""


def _row(cells: list[str], button_id: str | None) -> str:
    tds = "".join(f"<td>{c}</td>" for c in cells)
    if button_id:
        tds += f'<td><button id="{button_id}" name="{button_id}"><img/></button></td>'
    return f"<tr>{tds}</tr>"


def orientation_listing(rows: list[tuple[str, str, str, str]] | None = None,
                        view_state: str = "vs-1") -> str:
    rows = rows if rows is not None else [
        ("ORIENTACION", "17/07/2026 12:37", "Conoce el aplicativo ELSSA", "LEÍDO"),
        ("ORIENTACION", "16/06/2026 16:27", "Entornos laborales seguros", "NO LEÍDO"),
    ]
    headers = "".join(f"<th>{h}</th>" for h in
                      ("Categoría", "Fecha de Depósito", "Asunto", "Estado", "Opción"))
    body = "".join(
        _row(list(row), f"formOrientacionEmpleador:dtAlertas:{i}:j_idt82")
        for i, row in enumerate(rows)
    )
    return LISTING.format(
        form_id="formOrientacionEmpleador",
        path="/si.inbox/Orientacion/ListadoOrientacionEmpleador",
        table_id="formOrientacionEmpleador:dtAlertas",
        headers=headers, rows=body, view_state=view_state, modal=SESSION_MODAL,
    )


def requirement_listing(acknowledged: str = "01/09/2025 22:33 Efectos del Acuse") -> str:
    headers = "".join(f"<th>{h}</th>" for h in (
        "Tipo de Requerimiento", "Registro", "Fecha de Depósito",
        "Fecha Acuse de Recibo", "Fecha de Notificación", "Plazo",
        "Fecha Límite de Presentación",
    ))
    body = _row([
        "CARTAS INDUCTIVAS GENERAL", "0000141041 - 2025 - SUNAFIL/DPPR/SDPA",
        "28/08/2025 11:29", acknowledged, "02/09/2025", "20", "30/09/2025",
    ], None)
    return LISTING.format(
        form_id="formactosAdministrativos",
        path="/si.inbox/ActosAdministrativos/ListadoRequerimientos",
        table_id="formactosAdministrativos:dtAlertas",
        headers=headers, rows=body, view_state="vs-2", modal=SESSION_MODAL,
    )


def empty_notification_listing() -> str:
    headers = "".join(f"<th>{h}</th>" for h in
                      ("Orden de Inspección", "Intendencia", "Estado", "Ver Documentos"))
    return LISTING.format(
        form_id="formNotificacion",
        path="/si.inbox/Notificacion/Empleador",
        table_id="formNotificacion:dtNotificaciones",
        headers=headers, rows="", view_state="vs-3", modal=SESSION_MODAL,
    )


def orientation_detail() -> str:
    return """
    <html><body>
    <form id="formDetallePlantillaOrientacionEmpleador">
      <button id="btnActualizar"><span>ui-button</span></button>
      <script>var x = 1;</script>
      <img src="/si.inbox/faces/javax.faces.resource/logo.png?ln=images"/>
      <img src="https://casillaelectronica.sunafil.gob.pe/sunafil_te_orienta/banner.png"/>
      <p>Estimado empleador PATTERN GROUP S.A.C. - RUC( 20604442533 )</p>
      <a href="https://www.gob.pe/institucion/sunafil/campanas/142768-curso">Inscríbete</a>
      <a href="https://www.gob.pe/institucion/sunafil/campanas/142768-curso">Ver más</a>
    </form>
    </body></html>
    """


ENTRY_PAGE = """
<html><body>Bienvenido, cargando CLAVE SOL de SUNAT...</body>
<script>
  var pathurl = "https://api-seguridad.sunat.gob.pe";
  var cid = "b6474e23-8a3b-4153-b301-dafcc9646250";
  var st = "s";
  var redirectURL = "https://casillaelectronica.sunafil.gob.pe/si.inbox/Login/Empresa";
</script></html>
"""
