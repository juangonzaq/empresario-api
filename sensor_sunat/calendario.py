"""Generador de calendario tributario SUNAT — funciones puras, sin Django.

Todas las fechas salen de data/cronograma_<anio>.yaml; el código nunca las hardcodea.
"""

import hashlib
from datetime import date, timedelta
from pathlib import Path

import yaml

DATA = yaml.safe_load(
    (Path(__file__).parent / "data" / "cronograma_2026.yaml").read_text(encoding="utf-8")
)

# Último dígito del RUC → índice de columna en la tabla `mensual` (columna 6 = BC)
COL = {0: 0, 1: 1, 2: 2, 3: 2, 4: 3, 5: 3, 6: 4, 7: 4, 8: 5, 9: 5}
GRUPO = {0: "0", 1: "1", 2: "2-3", 3: "2-3", 4: "4-5", 5: "4-5", 6: "6-7", 7: "6-7", 8: "8-9", 9: "8-9"}

MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

LABORAL = {
    "cts_mayo": ("🔴 Depósito CTS (mayo)", "Depósito semestral de CTS de los trabajadores."),
    "cts_noviembre": ("🔴 Depósito CTS (noviembre)", "Depósito semestral de CTS de los trabajadores."),
    "gratificacion_julio": ("🔴 Gratificación de julio", "Pago de gratificación por Fiestas Patrias + bonificación 9%."),
    "gratificacion_diciembre": ("🔴 Gratificación de diciembre", "Pago de gratificación por Navidad + bonificación 9%."),
}


def validar_ruc(ruc: str) -> bool:
    """RUC de 11 dígitos, prefijo 10/15/17/20 y dígito verificador módulo 11."""
    if len(ruc) != 11 or not ruc.isdigit() or ruc[:2] not in ("10", "15", "17", "20"):
        return False
    pesos = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    suma = sum(int(c) * p for c, p in zip(ruc[:10], pesos))
    verificador = 11 - (suma % 11)
    if verificador == 10:
        verificador = 0
    elif verificador == 11:
        verificador = 1
    return verificador == int(ruc[10])


def grupo_de(ruc: str) -> str:
    return GRUPO[int(ruc[-1])]


def nombre_mes(periodo: str) -> str:
    """'202601' → 'enero 2026'."""
    return f"{MESES[int(periodo[4:6]) - 1]} {periodo[:4]}"


def _desc_mensual(planilla: bool) -> str:
    base = "Declaración y pago mensual: IGV-Renta (F.V. 621)"
    if planilla:
        base += " + PLAME (planilla electrónica)"
    return base + ". Vence según cronograma R.S. 281-2022/SUNAT."


def eventos_para(ruc: str, planilla=True, bc=False, regimen="RMT", desde: date = None) -> list[dict]:
    """Lista ordenada de eventos. Cada evento:
    {fecha: date|None, titulo, tipo, descripcion, alarmas_dias: [int], recurrencia: str|None}
    """
    d = int(ruc[-1])
    col = 6 if bc else COL[d]
    ev = []

    for periodo, fechas in DATA["mensual"].items():
        titulo = f"🔴 SUNAT vence: 621{' + PLAME' if planilla else ''} período {nombre_mes(periodo)}"
        ev.append(dict(
            fecha=date.fromisoformat(fechas[col]), tipo="SUNAT_MENSUAL", titulo=titulo,
            descripcion=_desc_mensual(planilla), alarmas_dias=[7, 2], recurrencia=None,
        ))

    if regimen in ("RMT", "RG"):  # RER y RUS no presentan DJ Anual
        if bc:
            f = date.fromisoformat(DATA["dj_anual"]["bc_mype"])
        else:
            f = date.fromisoformat(DATA["dj_anual"]["mype_ley31940"][d])
        ev.append(dict(
            fecha=f, tipo="DJ_ANUAL", titulo="🔴 DJ Anual Renta 2025 (F.V. 710)",
            descripcion="Cronograma MYPE Ley 31940. Si tus ingresos superan 1700 UIT, "
                        "aplica el cronograma general (marzo-abril).",
            alarmas_dias=[14, 3], recurrencia=None,
        ))

    if planilla:
        for k, (titulo, desc) in LABORAL.items():
            ev.append(dict(
                fecha=date.fromisoformat(DATA["laboral_fijo"][k]), tipo="LABORAL",
                titulo=titulo, descripcion=desc, alarmas_dias=[7, 2], recurrencia=None,
            ))
        ev.append(dict(
            fecha=None, tipo="AFP", titulo="🔴 Semana AFP: pagar antes del 5.º día hábil",
            descripcion="Retenciones de trabajadores: se paga primero, siempre. Vía AFPnet.",
            alarmas_dias=[], recurrencia="FREQ=MONTHLY;BYMONTHDAY=1",
        ))

    ev.append(dict(
        fecha=None, tipo="BUZON", titulo="📬 Revisar Buzón SOL + casilla SUNAFIL",
        descripcion="Las notificaciones surten efecto al depositarse.",
        alarmas_dias=[], recurrencia="FREQ=WEEKLY;BYDAY=MO",
    ))

    desde = desde or date.today()
    return sorted(
        [e for e in ev if e["fecha"] is None or e["fecha"] >= desde],
        key=lambda e: e["fecha"] or desde,
    )


def serializar(e: dict) -> dict:
    out = dict(e)
    out["fecha"] = e["fecha"].isoformat() if e["fecha"] else None
    return out


def _ics_escape(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _uid(ruc: str, tipo: str, fecha) -> str:
    clave = f"{ruc}{tipo}{fecha.isoformat() if fecha else 'recurrente'}"
    return f"vigia-{hashlib.sha1(clave.encode()).hexdigest()[:12]}@emprendedor.pe"


def _dtstart_recurrente(recurrencia: str, base: date) -> date:
    """Primera ocurrencia de la regla a partir de `base`."""
    if "BYMONTHDAY=1" in recurrencia:
        if base.day == 1:
            return base
        return (base.replace(day=1) + timedelta(days=32)).replace(day=1)
    if "BYDAY=MO" in recurrencia:
        return base + timedelta(days=(0 - base.weekday()) % 7)
    return base


def a_ics(ruc: str, eventos: list[dict], hoy: date = None) -> str:
    """Serializa a iCalendar RFC 5545 (sin dependencias externas)."""
    hoy = hoy or date.today()
    anio = int(DATA["anio"])
    until = f"{anio + 1}0131"  # fin de año + 1 mes: enero del año siguiente ya vive en el YAML
    lineas = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//emprendedor.pe//VIGIA Calendario SUNAT//ES",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:SUNAT · RUC {ruc}",
        "X-WR-TIMEZONE:America/Lima",
    ]
    for e in eventos:
        lineas.append("BEGIN:VEVENT")
        lineas.append(f"UID:{_uid(ruc, e['tipo'], e['fecha'])}")
        lineas.append(f"DTSTAMP:{hoy.strftime('%Y%m%d')}T000000Z")
        if e["fecha"] is not None:
            inicio = e["fecha"]
        else:
            inicio = _dtstart_recurrente(e["recurrencia"], hoy)
        lineas.append(f"DTSTART;VALUE=DATE:{inicio.strftime('%Y%m%d')}")
        lineas.append(f"DTEND;VALUE=DATE:{(inicio + timedelta(days=1)).strftime('%Y%m%d')}")
        if e["recurrencia"]:
            lineas.append(f"RRULE:{e['recurrencia']};UNTIL={until}T235959Z")
        lineas.append(f"SUMMARY:{_ics_escape(e['titulo'])}")
        lineas.append(f"DESCRIPTION:{_ics_escape(e['descripcion'])}")
        for n in e["alarmas_dias"]:
            lineas.append("BEGIN:VALARM")
            lineas.append(f"TRIGGER:-P{n}D")
            lineas.append("ACTION:DISPLAY")
            lineas.append(f"DESCRIPTION:{_ics_escape(e['titulo'])}")
            lineas.append("END:VALARM")
        lineas.append("END:VEVENT")
    lineas.append("END:VCALENDAR")
    return "\r\n".join(lineas) + "\r\n"
