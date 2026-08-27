"""El padrón de Sujetos Sin Capacidad Operativa, descargado de SUNAT.

SUNAT publica en
https://www.sunat.gob.pe/padronesnotificaciones/sujeSinCapacidadOperativa.html
un Excel con los RUC a los que ha atribuido, por resolución firme, la
condición de SSCO. Se actualiza a fin de mes. Es el filtro más fiable que
existe para un proveedor —viene directo de SUNAT y no admite discusión—, así
que se baja entero y se guarda como tabla global para cruzarlo con cualquier
cartera.

El Excel trae nueve columnas fijas; se leen por posición y se comprueba la
cabecera, para que un cambio de formato en SUNAT falle ruidosamente en vez de
guardar basura en silencio.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO

import requests
from django.db import transaction
from django.utils import timezone

from ..models import SujetoSinCapacidadOperativa

logger = logging.getLogger(__name__)

PADRON_URL = (
    "https://www.sunat.gob.pe/padronesnotificaciones/ssco/sujesincapacidadOperativa.xlsx"
)
USER_AGENT = "Mozilla/5.0 (compatible; Empresario/1.0)"
TIMEOUT = 60

# La cabecera tal como la escribe SUNAT (recortada: basta con el arranque).
CABECERA = ("RUC", "Razón social", "Domicilio fiscal", "Resolución")


class PadronSscoError(Exception):
    """No se pudo bajar o leer el padrón."""


@dataclass
class FilaSsco:
    ruc: str
    razon_social: str = ""
    domicilio_fiscal: str = ""
    resolucion: str = ""
    fecha_resolucion: date | None = None
    fecha_firme: date | None = None
    representante_documento: str = ""
    representante_nombre: str = ""
    fecha_publicacion: date | None = None


@dataclass
class ResultadoSsco:
    total: int = 0
    nuevos: int = 0
    actualizados: int = 0
    retirados: int = 0


def _texto(valor) -> str:
    return "" if valor is None else str(valor).strip()


def _fecha(valor) -> date | None:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = _texto(valor)
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def descargar(url: str = PADRON_URL) -> bytes:
    try:
        respuesta = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT,
        )
        respuesta.raise_for_status()
    except requests.RequestException as exc:
        raise PadronSscoError(f"No se pudo descargar el padrón SSCO: {exc}") from exc
    if not respuesta.content.startswith(b"PK"):
        raise PadronSscoError("La descarga del padrón SSCO no es un Excel.")
    return respuesta.content


def parsear(contenido: bytes) -> list[FilaSsco]:
    """Lee el Excel y devuelve una fila por RUC válido.

    El RUC viene como número (SUNAT lo escribe así en el Excel), por eso se
    normaliza a texto de 11 dígitos. Filas sin RUC de 11 dígitos se saltan:
    son separadores o notas al pie.
    """
    import openpyxl

    try:
        libro = openpyxl.load_workbook(BytesIO(contenido), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 — openpyxl lanza de todo
        raise PadronSscoError(f"El padrón SSCO no se pudo leer: {exc}") from exc

    hoja = libro.worksheets[0]
    filas = hoja.iter_rows(values_only=True)
    cabecera = tuple(_texto(c) for c in (next(filas, None) or ()))
    if not all(
        col and col.lower().startswith(esperada.lower())
        for col, esperada in zip(cabecera, CABECERA)
    ):
        raise PadronSscoError(f"El padrón SSCO cambió de formato: {cabecera[:4]}")

    resultado: list[FilaSsco] = []
    for fila in filas:
        celdas = list(fila) + [None] * 9
        ruc = _texto(celdas[0]).split(".")[0]
        if not (ruc.isdigit() and len(ruc) == 11):
            continue
        resultado.append(FilaSsco(
            ruc=ruc,
            razon_social=_texto(celdas[1]),
            domicilio_fiscal=_texto(celdas[2]),
            resolucion=_texto(celdas[3]),
            fecha_resolucion=_fecha(celdas[4]),
            fecha_firme=_fecha(celdas[5]),
            representante_documento=_texto(celdas[6]).split(".")[0],
            representante_nombre=_texto(celdas[7]),
            fecha_publicacion=_fecha(celdas[8]),
        ))
    return resultado


@transaction.atomic
def guardar(filas: list[FilaSsco], visto_el: date | None = None) -> ResultadoSsco:
    """Deja la tabla igual al padrón: alta, actualización y retiro.

    Un RUC que ya no aparece pasa a ``vigente=False`` en vez de borrarse: que
    un proveedor *estuvo* en el padrón sigue siendo un dato para las facturas
    de esas fechas.
    """
    visto_el = visto_el or timezone.localdate()
    resultado = ResultadoSsco(total=len(filas))
    existentes = {s.ruc: s for s in SujetoSinCapacidadOperativa.objects.all()}
    campos = (
        "razon_social", "domicilio_fiscal", "resolucion", "fecha_resolucion",
        "fecha_firme", "representante_documento", "representante_nombre",
        "fecha_publicacion",
    )

    nuevos: list[SujetoSinCapacidadOperativa] = []
    for fila in filas:
        actual = existentes.get(fila.ruc)
        if actual is None:
            nuevos.append(SujetoSinCapacidadOperativa(
                ruc=fila.ruc, vigente=True, visto_el=visto_el,
                **{campo: getattr(fila, campo) for campo in campos},
            ))
            continue
        cambios = {
            campo: getattr(fila, campo) for campo in campos
            if getattr(actual, campo) != getattr(fila, campo)
        }
        if not actual.vigente:
            cambios["vigente"] = True
        if cambios:
            resultado.actualizados += 1
        for campo, valor in cambios.items():
            setattr(actual, campo, valor)
        actual.visto_el = visto_el
        actual.save(update_fields=[*cambios, "visto_el", "updated_at"])

    SujetoSinCapacidadOperativa.objects.bulk_create(nuevos, ignore_conflicts=True)
    resultado.nuevos = len(nuevos)

    # Solo se retira si el padrón trajo algo: un Excel vacío es un fallo de
    # SUNAT, no una amnistía general.
    if filas:
        presentes = {fila.ruc for fila in filas}
        resultado.retirados = (
            SujetoSinCapacidadOperativa.objects.vigentes()
            .exclude(ruc__in=presentes)
            .update(vigente=False)
        )
    return resultado


def sincronizar_padron(url: str = PADRON_URL) -> ResultadoSsco:
    filas = parsear(descargar(url))
    resultado = guardar(filas)
    logger.info(
        "Padrón SSCO: %s RUC (%s nuevos, %s actualizados, %s retirados)",
        resultado.total, resultado.nuevos, resultado.actualizados, resultado.retirados,
    )
    return resultado


def rucs_en_padron(rucs) -> dict[str, SujetoSinCapacidadOperativa]:
    """Cuáles de estos RUC están hoy en el padrón (vigentes)."""
    rucs = {r for r in rucs if r}
    if not rucs:
        return {}
    return {
        s.ruc: s
        for s in SujetoSinCapacidadOperativa.objects.vigentes().filter(ruc__in=rucs)
    }


__all__ = [
    "FilaSsco",
    "PadronSscoError",
    "ResultadoSsco",
    "guardar",
    "parsear",
    "rucs_en_padron",
    "sincronizar_padron",
]
