"""De qué empresa es el calendario, y qué vencimientos le tocan.

El generador de `calendario.py` necesita tres cosas además del RUC: si hay
planilla, si la empresa es Buen Contribuyente y en qué régimen tributa. La
pantalla las preguntaba las tres. Dos de ellas ya las sabemos:

* **planilla** — la ficha RUC publica el número de trabajadores declarados en
  PLAME (``RucSnapshot.worker_count``).
* **Buen Contribuyente** — aparece en los registros de la ficha, y mueve todos
  los vencimientos mensuales a la columna especial del cronograma.

La tercera, el **régimen**, no la publica ninguna fuente que sincronicemos, así
que la declara la empresa una vez y se guarda en ``Organization.tax_regime``.

Cada dato lleva su procedencia, y eso no es decorativo: un vencimiento que sale
de un supuesto nuestro no puede presentarse igual que uno derivado de la ficha
RUC oficial. La pantalla lo dice, para que nadie planifique un pago sobre una
suposición creyendo que es un dato.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from accounts.models import Organization, TaxRegime

# Régimen que se asume mientras la empresa no declare el suyo. Es el más común
# entre las MYPE y, frente al RER, peca por exceso: incluye la DJ Anual, así
# que como mucho sobra un vencimiento —nunca falta uno.
REGIMEN_POR_DEFECTO = TaxRegime.RMT

# Cómo aparece la condición en el texto de registros de la ficha RUC.
MARCA_BUEN_CONTRIBUYENTE = "BUEN CONTRIBUYENTE"

# Tipos de contribuyente que no pueden acogerse al Nuevo RUS. No se les quita
# la opción —quien tributa sabe mejor que nosotros en qué régimen está— pero sí
# se avisa, porque elegir RUS por descuido esconde la DJ Anual del calendario.
TIPOS_SIN_RUS = ("SOCIEDAD", "S.A.", "S.A.C.", "S.R.L.", "ASOCIACION")

NOTA_SIN_RUS = (
    "El Nuevo RUS no aplica a sociedades. Si lo eliges, el calendario dejará "
    "de mostrarte la Declaración Jurada Anual."
)


@dataclass(frozen=True)
class DatoDerivado:
    """Un valor y de dónde salió."""

    valor: bool
    #: "ficha" (ficha RUC), "declarado" (lo dijo la empresa) o "supuesto".
    origen: str
    detalle: str = ""

    def as_dict(self) -> dict:
        return {"valor": self.valor, "origen": self.origen, "detalle": self.detalle}


@dataclass(frozen=True)
class ContextoCalendario:
    ruc: str
    regimen: str
    regimen_declarado: bool
    planilla: DatoDerivado
    buen_contribuyente: DatoDerivado
    trabajadores: int | None
    nota_regimen: str = ""
    avisos: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ruc": self.ruc,
            "regimen": self.regimen,
            "regimen_declarado": self.regimen_declarado,
            "planilla": self.planilla.as_dict(),
            "buen_contribuyente": self.buen_contribuyente.as_dict(),
            "trabajadores": self.trabajadores,
            "nota_regimen": self.nota_regimen,
            "avisos": self.avisos,
        }


def _ultimo_snapshot(ruc: str):
    """La ficha RUC más reciente que se capturó bien.

    Se descartan las fallidas a propósito: un scrapeo que reventó a medias deja
    ``worker_count`` en cero, y tomarlo por bueno haría desaparecer del
    calendario la CTS y las gratificaciones de una empresa que sí tiene
    planilla.
    """
    from ruc_profile.models import RucSnapshot

    return (
        RucSnapshot.objects.filter(ruc=ruc, succeeded=True)
        .order_by("-captured_on", "-created_at")
        .first()
    )


def _planilla_de(snapshot) -> DatoDerivado:
    if snapshot is None or snapshot.worker_count is None:
        return DatoDerivado(
            valor=True,
            origen="supuesto",
            detalle=(
                "Todavía no hemos leído tu ficha RUC. Asumimos que tienes "
                "planilla para no ocultarte vencimientos laborales."
            ),
        )
    tiene = snapshot.worker_count > 0
    periodo = snapshot.latest_worker_period or "el último período publicado"
    return DatoDerivado(
        valor=tiene,
        origen="ficha",
        detalle=(
            f"{snapshot.worker_count} trabajador(es) declarados en PLAME "
            f"({periodo})."
            if tiene
            else f"Sin trabajadores declarados en PLAME ({periodo})."
        ),
    )


def _buen_contribuyente_de(snapshot) -> DatoDerivado:
    if snapshot is None:
        return DatoDerivado(
            valor=False,
            origen="supuesto",
            detalle="Todavía no hemos leído tu ficha RUC.",
        )
    registros = (snapshot.registries or "").upper()
    incluido = MARCA_BUEN_CONTRIBUYENTE in registros
    return DatoDerivado(
        valor=incluido,
        origen="ficha",
        detalle=(
            "Incluida en el Régimen de Buen Contribuyente: tus vencimientos "
            "mensuales usan la columna especial del cronograma."
            if incluido
            else "No figura en el Régimen de Buen Contribuyente."
        ),
    )


def _nota_regimen(snapshot) -> str:
    if snapshot is None:
        return ""
    tipo = (snapshot.taxpayer_type or "").upper()
    return NOTA_SIN_RUS if any(m in tipo for m in TIPOS_SIN_RUS) else ""


def contexto_de(organization: Organization) -> ContextoCalendario:
    """Reúne todo lo que hace falta para generar el calendario de una empresa."""
    snapshot = _ultimo_snapshot(organization.ruc)
    planilla = _planilla_de(snapshot)
    buen_contribuyente = _buen_contribuyente_de(snapshot)

    declarado = bool(organization.tax_regime)
    avisos: list[str] = []
    if not declarado:
        avisos.append(
            f"Asumimos régimen {REGIMEN_POR_DEFECTO.label}. Declara el tuyo "
            "para que los vencimientos sean exactos."
        )
    if planilla.origen == "supuesto":
        avisos.append(planilla.detalle)

    return ContextoCalendario(
        ruc=organization.ruc,
        regimen=organization.tax_regime or REGIMEN_POR_DEFECTO,
        regimen_declarado=declarado,
        planilla=planilla,
        buen_contribuyente=buen_contribuyente,
        trabajadores=snapshot.worker_count if snapshot else None,
        nota_regimen=_nota_regimen(snapshot),
        avisos=avisos,
    )


def eventos_de(organization: Organization, desde: date | None = None) -> list[dict]:
    """Los vencimientos de esta empresa, ya resueltos sus parámetros."""
    from .calendario import eventos_para

    contexto = contexto_de(organization)
    return eventos_para(
        organization.ruc,
        planilla=contexto.planilla.valor,
        bc=contexto.buen_contribuyente.valor,
        regimen=contexto.regimen,
        desde=desde,
    )
