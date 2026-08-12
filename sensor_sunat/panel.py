"""Lo que el empresario necesita ver de un vistazo, en una sola respuesta.

El panel del calendario no es solo el cronograma: junta las tres cosas que
tienen fecha o urgencia y que hoy vivían en pantallas distintas.

* **Vencimientos** — del cronograma oficial, derivados del dígito del RUC.
* **Alertas financieras** — las que ya calcula ``finance_analytics`` sobre los
  comprobantes: detracciones, importes, incoherencias.
* **Buzón SUNAT** — los mensajes que el análisis marcó como que exigen acción.

Van juntos porque compiten por la misma decisión —«¿qué hago hoy?»— y
repartidos en tres pantallas obligaban a cruzarlos de cabeza.

Una advertencia sobre los plazos del buzón: se usan solo cuando el análisis
guardó **la fuente** de la que salió la fecha. Un plazo legal inventado por un
modelo es peor que no tener plazo, porque se planifica sobre él.
"""

from __future__ import annotations

from datetime import date

from accounts.models import Organization

# Cuántos elementos entran en el panel. Es un cajón de urgencias, no un
# listado: cada pantalla completa sigue existiendo detrás de su enlace.
TOPE_VENCIMIENTOS = 5
TOPE_ALERTAS = 5
TOPE_BUZON = 5

# A partir de aquí un vencimiento se pinta como urgente.
DIAS_URGENTE = 7


def _dias_hasta(fecha: date, hoy: date) -> int:
    return (fecha - hoy).days


def proximos_vencimientos(
    organization: Organization, hoy: date, tope: int = TOPE_VENCIMIENTOS
) -> list[dict]:
    from .contexto import eventos_de

    eventos = [e for e in eventos_de(organization, desde=hoy) if e["fecha"]]
    return [
        {
            "fecha": e["fecha"].isoformat(),
            "titulo": e["titulo"],
            "tipo": e["tipo"],
            "descripcion": e["descripcion"],
            "dias": _dias_hasta(e["fecha"], hoy),
        }
        for e in eventos[:tope]
    ]


def alertas_financieras(ruc: str, tope: int = TOPE_ALERTAS) -> list[dict]:
    """Las alertas abiertas que valen una decisión, la más grave primero."""
    from finance_analytics.models import SEVERITY_RANK, FinanceAlert

    abiertas = list(FinanceAlert.objects.filter(account_ruc=ruc).open())
    abiertas.sort(key=lambda a: (SEVERITY_RANK.get(a.severity, 9), a.period))
    return [
        {
            "id": str(a.id),
            "titulo": a.title,
            "severidad": a.severity,
            "periodo": a.period,
            "explicacion": a.explanation,
            "recomendacion": a.recommendation,
            "importe": str(a.amount) if a.amount is not None else None,
            "moneda": a.currency,
        }
        for a in abiertas[:tope]
    ]


def buzon_pendiente(ruc: str, tope: int = TOPE_BUZON) -> list[dict]:
    """Mensajes del buzón SUNAT que el análisis marcó como accionables.

    Se ordenan por plazo legal cuando lo hay —con fuente citada— y por
    prioridad y fecha de publicación cuando no.
    """
    from sunat_intel.models import AnalysisStatus, MessageAnalysis, Priority

    orden = {Priority.CRITICAL: 0, Priority.HIGH: 1, Priority.MEDIUM: 2}
    analisis = (
        MessageAnalysis.objects.filter(
            message__taxpayer_id=ruc,
            status=AnalysisStatus.DONE,
            requires_action=True,
            priority__in=[Priority.CRITICAL, Priority.HIGH, Priority.MEDIUM],
        )
        .select_related("message")
        .order_by("-message__published_at")
    )

    filas = []
    for a in analisis[: tope * 4]:
        # Sin fuente citada la fecha no se presenta como plazo: el propio
        # `apply_result` ya la descarta, y esto lo vuelve explícito aquí.
        plazo = a.legal_deadline if (a.legal_deadline and a.deadline_source) else None
        filas.append({
            "id": str(a.message_id),
            "asunto": a.message.subject or "(sin asunto)",
            "tipo": a.comm_type,
            "prioridad": a.priority,
            "resumen": a.summary,
            "accion": a.next_action,
            "plazo": plazo.isoformat() if plazo else None,
            "plazo_fuente": a.deadline_source if plazo else "",
            "publicado": a.message.published_at.date().isoformat()
            if a.message.published_at
            else None,
        })

    filas.sort(key=lambda f: (
        f["plazo"] is None,                      # con plazo, primero
        f["plazo"] or "",
        orden.get(f["prioridad"], 9),
    ))
    return filas[:tope]


def resumen(organization: Organization, hoy: date) -> dict:
    """Versión mínima para el icono del navbar.

    La consulta el badge cada vez que se pinta la barra, así que devuelve
    contadores y el vencimiento más cercano, nada más.
    """
    from finance_analytics.models import FinanceAlert
    from sunat_intel.models import AnalysisStatus, MessageAnalysis, Priority

    ruc = organization.ruc
    vencimientos = proximos_vencimientos(organization, hoy, tope=1)
    proximo = vencimientos[0] if vencimientos else None

    alertas = FinanceAlert.objects.filter(account_ruc=ruc).open().priority().count()
    buzon = MessageAnalysis.objects.filter(
        message__taxpayer_id=ruc,
        status=AnalysisStatus.DONE,
        requires_action=True,
        priority__in=[Priority.CRITICAL, Priority.HIGH],
    ).count()

    dias = proximo["dias"] if proximo else None
    if dias is None:
        urgencia = "ninguna"
    elif dias <= 2:
        urgencia = "critica"
    elif dias <= DIAS_URGENTE:
        urgencia = "alta"
    else:
        urgencia = "normal"

    return {
        "proximo": proximo,
        "dias": dias,
        "urgencia": urgencia,
        "alertas_prioritarias": alertas,
        "buzon_pendiente": buzon,
        # Lo que se pinta dentro del punto rojo: un número corto o nada.
        "badge": str(dias) if dias is not None and dias <= DIAS_URGENTE else "",
    }
