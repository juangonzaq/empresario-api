"""¿Tiene sentido que le compres a este proveedor, viendo a qué se dedica?

La ficha RUC de SUNAT lista las actividades económicas con su código CIIU:
«Principal - 4663 - VENTA AL POR MAYOR DE MATERIALES DE CONSTRUCCIÓN…». Un
auditor cruza eso con la actividad de la empresa compradora: una consultora
de software con treinta facturas de una ferretería tiene que explicar qué
compró.

Los códigos no se comparan entre sí a propósito. SUNAT mezcla revisiones del
CIIU —fichas antiguas con la rev. 3 de cinco dígitos (60214), recientes con la
rev. 4 de cuatro (4922)— y el mismo número significa cosas distintas en cada
una. Lo estable es la descripción, así que se clasifica por palabras clave en
*sectores* gruesos, y se pregunta si el sector del proveedor es plausible para
el de la empresa. Es un cedazo, no una taxonomía: está para destacar lo que
chirría, no para certificar lo que encaja.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Sector → palabras que lo delatan en la descripción de SUNAT (sin tildes y en
# minúsculas, que es como se normaliza el texto antes de buscar).
SECTORES: dict[str, tuple[str, ...]] = {
    "construccion": (
        "construccion", "ferreteria", "fontaneria", "edificio", "obras",
        "cemento", "ladrillo", "pintura", "vidrio", "madera", "acabado",
    ),
    "transporte": (
        "transporte", "carga", "mudanza", "courier", "mensajeria", "logistica",
        "almacenamiento", "deposito", "agencia de viaje", "turistic",
    ),
    "telecomunicaciones": ("telecomunicac", "internet", "telefon", "cable"),
    "informatica": (
        "informatic", "software", "programacion", "sistemas", "computador",
        "tecnologia de la informacion", "procesamiento de datos",
    ),
    "servicios_profesionales": (
        "consultor", "asesor", "contab", "juridic", "legal", "auditor",
        "arquitect", "ingenier", "publicidad", "marketing", "investigacion de mercado",
        "diseno", "actividades profesionales", "gestion empresarial",
    ),
    "inmobiliario": ("inmobiliar", "alquiler", "arrendamiento", "bienes raices"),
    "alimentos": (
        "alimento", "restaurante", "comida", "bebida", "panader", "carne",
        "pescado", "lacteo", "fruta", "verdura", "catering", "bar ",
    ),
    "hoteleria": ("hotel", "hospedaje", "alojamiento"),
    "textil": ("textil", "prenda", "vestir", "calzado", "confeccion", "tejido"),
    "manufactura": (
        "fabricacion", "elaboracion", "produccion de", "industria", "plastico",
        "quimic", "jabon", "detergente", "metal", "papel", "caucho", "muebles",
    ),
    "comercio_general": (
        "otros productos", "productos nuevos", "no especializad", "supermercado",
        "bodega", "abarrotes", "por mayor de otros", "venta al por menor de otros",
        "mercaderia", "productos diversos",
    ),
    "vehiculos": ("vehiculo", "automotor", "repuesto", "taller", "combustible", "grifo"),
    "salud": ("salud", "medic", "clinic", "farmac", "odont", "hospital"),
    "educacion": ("educacion", "ensenanza", "capacitacion", "academia", "colegio"),
    "agro": ("agricol", "agropecuar", "cultivo", "ganad", "pesca", "avicol", "forestal"),
    "mineria": ("miner", "extraccion", "canter", "hidrocarburo", "petrol"),
    "financiero": ("financier", "banc", "seguro", "credito", "cobranza"),
    "seguridad_limpieza": ("seguridad", "vigilancia", "limpieza", "mantenimiento", "jardin"),
    "energia": ("electric", "energia", "agua", "gas", "generacion"),
    "entretenimiento": ("entretenimiento", "espectaculo", "eventos", "deport", "recreat"),
}

# Sectores a los que cualquier empresa les compra con naturalidad: oficina,
# sistemas, asesores, local, transporte, comida para el equipo. Un proveedor
# de estos nunca «chirría» por su actividad, sea cual sea la del comprador.
INSUMOS_UNIVERSALES = frozenset({
    "informatica", "servicios_profesionales", "inmobiliario", "transporte",
    "telecomunicaciones", "comercio_general", "seguridad_limpieza", "alimentos",
    "hoteleria", "financiero", "energia", "vehiculos", "educacion",
})

# Afinidades entre sectores no universales: quién le compra a quién de forma
# evidente. Se lee «la empresa del sector X le compra a proveedores de Y».
AFINES: dict[str, frozenset[str]] = {
    "construccion": frozenset({"construccion", "manufactura", "mineria", "vehiculos"}),
    "manufactura": frozenset({"manufactura", "agro", "mineria", "textil", "construccion"}),
    "textil": frozenset({"textil", "manufactura"}),
    "alimentos": frozenset({"alimentos", "agro", "manufactura"}),
    "agro": frozenset({"agro", "manufactura", "construccion"}),
    "mineria": frozenset({"mineria", "manufactura", "construccion"}),
    "salud": frozenset({"salud", "manufactura"}),
    "hoteleria": frozenset({"hoteleria", "alimentos", "construccion", "textil"}),
    "entretenimiento": frozenset({"entretenimiento", "alimentos"}),
    "vehiculos": frozenset({"vehiculos", "manufactura"}),
}

_ACTIVIDAD = re.compile(
    r"(Principal|Secundaria\s*\d*)\s*-\s*(?:CIIU\s*)?(\d{3,5})?\s*-?\s*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Actividad:
    rol: str          # Principal | Secundaria 1 | …
    codigo: str       # CIIU tal como lo escribe SUNAT ("" si no lo trae)
    descripcion: str

    @property
    def sectores(self) -> frozenset[str]:
        return sectores_de(self.descripcion)


def _plano(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sin_tildes.lower()


def sectores_de(descripcion: str) -> frozenset[str]:
    texto = _plano(descripcion)
    return frozenset(
        sector for sector, claves in SECTORES.items()
        if any(clave in texto for clave in claves)
    )


def parsear_actividades(texto: str) -> list[Actividad]:
    """Trocea el campo tal como lo deja el scraper, en una línea o en varias.

    Sirve para las dos formas que SUNAT ha usado: «Principal - CIIU 60214 -
    DESC.» y «Principal - 4663 - DESC». Lo que no se reconoce se ignora: mejor
    un análisis sin actividades que uno con basura.
    """
    if not texto:
        return []
    partes = _ACTIVIDAD.split(texto)
    # split deja: [antes, rol, codigo, desc, rol, codigo, desc, …]
    actividades = []
    for i in range(1, len(partes) - 2, 3):
        rol, codigo, desc = partes[i], partes[i + 1] or "", partes[i + 2]
        desc = desc.strip(" .-\n")
        if desc:
            actividades.append(Actividad(rol.strip().title(), codigo, desc))
    return actividades


@dataclass
class Compatibilidad:
    compatible: bool
    # Por qué se decidió; vacío cuando no hubo con qué comparar.
    motivo: str = ""
    actividad_proveedor: Actividad | None = None
    actividad_empresa: Actividad | None = None


def compatibilidad(
    actividades_empresa: str, actividades_proveedor: str,
) -> Compatibilidad:
    """¿Es plausible que una empresa con estas actividades le compre a esta otra?

    Compatible si algún sector del proveedor es un insumo universal, coincide
    con un sector de la empresa o le es afín. Si no se reconoce ningún sector
    en alguno de los dos, no se opina: la señal se apoya en lo que se sabe.
    """
    empresa = parsear_actividades(actividades_empresa)
    proveedor = parsear_actividades(actividades_proveedor)
    if not empresa or not proveedor:
        return Compatibilidad(compatible=True)

    sectores_empresa = frozenset().union(*(a.sectores for a in empresa))
    sectores_proveedor = frozenset().union(*(a.sectores for a in proveedor))
    if not sectores_empresa or not sectores_proveedor:
        return Compatibilidad(compatible=True)

    plausibles = set(INSUMOS_UNIVERSALES) | set(sectores_empresa)
    for sector in sectores_empresa:
        plausibles |= AFINES.get(sector, frozenset())

    if sectores_proveedor & plausibles:
        return Compatibilidad(compatible=True)

    return Compatibilidad(
        compatible=False,
        motivo=(
            f"Se dedica a «{proveedor[0].descripcion.lower()}» y tu empresa a "
            f"«{empresa[0].descripcion.lower()}»: no es un insumo evidente de tu "
            f"actividad, y es lo primero que un auditor pediría sustentar."
        ),
        actividad_proveedor=proveedor[0],
        actividad_empresa=empresa[0],
    )


__all__ = [
    "Actividad",
    "Compatibilidad",
    "compatibilidad",
    "parsear_actividades",
    "sectores_de",
]
