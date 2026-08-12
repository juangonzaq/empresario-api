"""Lectura de lo que devuelve AFPnet.

Cuatro formatos distintos, uno por pantalla, y ninguno es una API pensada para
esto:

* **Resumen por devengue** — fragmento HTML con una tabla por mes.
* **Planillas** — HTML con tablas anidadas: una fila agrupa el devengue y la
  siguiente contiene la tabla con sus planillas.
* **Deudas** — texto plano delimitado por ``|``, con cabecera y leyenda.
* **Historial del afiliado** — JSON, el único con forma de API.

Todos los parsers devuelven dataclases y ninguno toca la base de datos: así se
prueban contra las respuestas reales guardadas en ``tests/fixtures`` sin
necesidad de sesión ni de red.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from bs4 import BeautifulSoup

# La tabla buena de la pantalla de planillas. Se ancla por id porque la
# respuesta trae además una copia para impresión: sin esto, cada planilla se
# contaría dos veces.
TABLA_PLANILLAS = "gvw-planilla"

# Códigos de AFP tal como los espera el portal en `CodigoAFP`.
AFP_POR_CODIGO = {
    "HA": "habitat",
    "HO": "horizonte",
    "IN": "integra",
    "RI": "prima",
    "PR": "profuturo",
    "NV": "union_vida",
}
# …y como aparecen escritos en las tablas.
AFP_POR_NOMBRE = {
    "HABITAT": "habitat",
    "HORIZONTE": "horizonte",
    "INTEGRA": "integra",
    "PRIMA": "prima",
    "PROFUTURO": "profuturo",
    "UNION VIDA": "union_vida",
}


def _texto(nodo) -> str:
    return nodo.get_text(" ", strip=True) if nodo else ""


def a_decimal(valor: Any) -> Decimal | None:
    """«1,370.00» → Decimal('1370.00'). Devuelve None si no hay número.

    El separador de miles se quita antes de convertir: sin eso, ``Decimal``
    lanza y un importe de cuatro cifras se perdía en silencio.
    """
    if valor is None:
        return None
    texto = str(valor).strip().replace(",", "")
    if not texto or texto in {"-", "--"}:
        return None
    try:
        return Decimal(texto)
    except InvalidOperation:
        return None


def a_fecha(valor: str | None) -> date | None:
    """Acepta los dos formatos que mezcla el portal: dd/mm/aaaa y aaaa-mm-dd."""
    if not valor:
        return None
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(valor.strip(), formato).date()
        except ValueError:
            continue
    return None


def a_periodo(valor: str | None) -> str:
    """«2026-07» o «202607» → «202607». Cadena vacía si no se reconoce."""
    if not valor:
        return ""
    digitos = re.sub(r"\D", "", valor)
    return digitos if len(digitos) == 6 else ""


# ── Resumen de situación de obligaciones de pago ──────────────────────────

@dataclass
class ResumenDevengue:
    periodo: str
    total_op: int = 0
    op_cierta: int = 0
    op_presunta: int = 0
    op_con_deuda: int = 0
    op_sin_deuda: int = 0
    semaforo: str = ""

    @property
    def tiene_deuda(self) -> bool:
        return self.op_con_deuda > 0


def _entero(valor: str) -> int:
    digitos = re.sub(r"\D", "", valor or "")
    return int(digitos) if digitos else 0


def parsear_resumen_situacion(html: str) -> list[ResumenDevengue]:
    """Una fila por devengue: cuántas obligaciones hay y en qué estado."""
    sopa = BeautifulSoup(html, "html.parser")
    filas: list[ResumenDevengue] = []

    for tabla in sopa.find_all("table"):
        cabeceras = [_texto(th).lower() for th in tabla.find_all("th")]
        if not cabeceras or "devengue" not in cabeceras[0]:
            continue
        for tr in tabla.find_all("tr"):
            celdas = [_texto(td) for td in tr.find_all("td")]
            periodo = a_periodo(celdas[0]) if celdas else ""
            if not periodo:
                continue
            valores = celdas + [""] * 6
            filas.append(ResumenDevengue(
                periodo=periodo,
                total_op=_entero(valores[1]),
                op_cierta=_entero(valores[2]),
                op_presunta=_entero(valores[3]),
                semaforo=valores[4].strip(),
                op_con_deuda=_entero(valores[5]),
                op_sin_deuda=_entero(valores[6]),
            ))
        break  # la primera tabla con esa cabecera es la buena
    return filas


# ── Planillas ─────────────────────────────────────────────────────────────

@dataclass
class Planilla:
    periodo: str
    afp: str
    numero: str = ""
    nominal_fondo: Decimal | None = None
    nominal_ryr: Decimal | None = None
    estado: str = ""
    tipo_trabajador: str = ""
    fecha_declaracion: date | None = None
    fecha_pago: date | None = None
    ticket: str = ""
    banco: str = ""
    forma_pago: str = ""

    @property
    def pagada(self) -> bool:
        return self.estado.upper() == "PAGADA"


# Columnas de la tabla anidada, en el orden en que las manda el portal.
COLUMNAS_PLANILLA = [
    "afp", "numero", "nominal_fondo", "nominal_ryr", "estado",
    "tipo_trabajador", "fecha_declaracion", "fecha_pago", "ticket",
    "banco", "forma_pago",
]


def parsear_planillas(html: str) -> list[Planilla]:
    """Las planillas presentadas, con el devengue que las agrupa.

    La tabla principal alterna dos clases de fila: una con el devengue y sus
    totales, y otra que solo contiene la tabla con el detalle de ese mes. El
    devengue se arrastra de la primera a la segunda porque el detalle no lo
    repite.
    """
    sopa = BeautifulSoup(html, "html.parser")
    tabla = sopa.find("table", id=TABLA_PLANILLAS)
    if tabla is None:
        return []

    planillas: list[Planilla] = []
    periodo_actual = ""

    for tr in tabla.find_all("tr"):
        anidada = tr.find("table")
        if anidada is None:
            # Fila de agrupación: de aquí sale el devengue de las siguientes.
            celdas = [_texto(td) for td in tr.find_all("td")]
            for celda in celdas:
                periodo = a_periodo(celda)
                if periodo:
                    periodo_actual = periodo
                    break
            continue

        for fila in anidada.find_all("tr"):
            celdas = [_texto(td) for td in fila.find_all("td")]
            if len(celdas) < len(COLUMNAS_PLANILLA):
                continue
            datos = dict(zip(COLUMNAS_PLANILLA, celdas))
            planillas.append(Planilla(
                periodo=periodo_actual,
                afp=AFP_POR_NOMBRE.get(datos["afp"].upper(), "desconocida"),
                numero=datos["numero"],
                nominal_fondo=a_decimal(datos["nominal_fondo"]),
                nominal_ryr=a_decimal(datos["nominal_ryr"]),
                estado=datos["estado"],
                tipo_trabajador=datos["tipo_trabajador"],
                fecha_declaracion=a_fecha(datos["fecha_declaracion"]),
                fecha_pago=a_fecha(datos["fecha_pago"]),
                ticket=datos["ticket"],
                banco=datos["banco"],
                forma_pago=datos["forma_pago"],
            ))
    return planillas


# ── Deudas ciertas y presuntas ────────────────────────────────────────────

@dataclass
class FilaDeuda:
    cuspp: str
    documento: str
    nombre: str
    afp: str
    periodo: str
    tipo: str              # C: cierta · P: presunta
    deuda_fondo: Decimal | None = None
    deuda_seguro: Decimal | None = None
    deuda_comision: Decimal | None = None
    estado_cobranza: str = ""   # A: administrativa · J: judicial
    origen: str = ""


@dataclass
class ReporteDeuda:
    ruc: str = ""
    razon_social: str = ""
    devengue_maximo: str = ""
    actualizado_en: date | None = None
    filas: list[FilaDeuda] = field(default_factory=list)

    @property
    def sin_deuda(self) -> bool:
        return not self.filas


CAMPOS_DEUDA = [
    "cuspp", "documento", "nombre", "afp", "periodo", "tipo",
    "deuda_fondo", "deuda_seguro", "deuda_comision", "estado_cobranza",
    "origen",
]


def parsear_deudas(texto: str) -> ReporteDeuda:
    """El reporte de deuda, que llega como texto delimitado por «|».

    Sin filas significa **sin deuda**, y eso es un dato, no un fallo: es la
    diferencia entre «no debes nada» y «no pudimos consultarlo», que en una
    pantalla de cumplimiento no se pueden confundir.
    """
    reporte = ReporteDeuda()
    encabezado_visto = False

    for linea in texto.splitlines():
        limpia = linea.strip()
        if not limpia:
            continue
        if limpia.lower().startswith("leyenda"):
            break

        if "|" not in limpia:
            if ":" in limpia:
                etiqueta, _, valor = limpia.partition(":")
                etiqueta, valor = etiqueta.strip().lower(), valor.strip()
                if "devengue" in etiqueta:
                    reporte.devengue_maximo = a_periodo(valor)
                elif "actualizaci" in etiqueta:
                    reporte.actualizado_en = a_fecha(valor)
            elif re.match(r"^\d{11}\s*-", limpia):
                ruc, _, razon = limpia.partition("-")
                reporte.ruc, reporte.razon_social = ruc.strip(), razon.strip()
            continue

        partes = [p.strip() for p in limpia.split("|")]
        if not encabezado_visto:
            # La primera línea con «|» es la cabecera de columnas.
            encabezado_visto = True
            continue
        if len(partes) < len(CAMPOS_DEUDA):
            continue
        datos = dict(zip(CAMPOS_DEUDA, partes))
        reporte.filas.append(FilaDeuda(
            cuspp=datos["cuspp"],
            documento=datos["documento"],
            nombre=datos["nombre"],
            afp=AFP_POR_NOMBRE.get(datos["afp"].upper(), "desconocida"),
            periodo=a_periodo(datos["periodo"]),
            tipo=datos["tipo"],
            deuda_fondo=a_decimal(datos["deuda_fondo"]),
            deuda_seguro=a_decimal(datos["deuda_seguro"]),
            deuda_comision=a_decimal(datos["deuda_comision"]),
            estado_cobranza=datos["estado_cobranza"],
            origen=datos["origen"],
        ))
    return reporte


# ── Ficha del afiliado ────────────────────────────────────────────────────

@dataclass
class Afiliado:
    tipo_documento: str = ""
    numero_documento: str = ""
    apellido_paterno: str = ""
    apellido_materno: str = ""
    nombres: str = ""
    cuspp: str = ""
    devengue_maximo: str = ""
    motivo_pension: str = ""
    ultimo_devengue: str = ""
    situacion: str = ""
    afp: str = "desconocida"
    tipo_comision: str = ""
    porcentaje_comision: Decimal | None = None

    @property
    def nombre_completo(self) -> str:
        partes = [self.nombres, self.apellido_paterno, self.apellido_materno]
        return " ".join(p for p in partes if p).strip()


COLUMNAS_AFILIADO = [
    "documento", "apellido_paterno", "apellido_materno", "nombres", "cuspp",
    "devengue_maximo", "motivo_pension", "ultimo_devengue", "situacion",
    "afp", "tipo_comision", "porcentaje_comision",
]


def parsear_afiliado(html: str) -> Afiliado | None:
    """La ficha que devuelve la consulta por documento, o None si no hay."""
    sopa = BeautifulSoup(html, "html.parser")

    for tabla in sopa.find_all("table"):
        cabeceras = [_texto(th).lower() for th in tabla.find_all("th")]
        if not any("cuspp" in c for c in cabeceras):
            continue
        for tr in tabla.find_all("tr"):
            celdas = [_texto(td) for td in tr.find_all("td")]
            if len(celdas) < len(COLUMNAS_AFILIADO):
                continue
            datos = dict(zip(COLUMNAS_AFILIADO, celdas))
            # «DNI - 12345678» viene en una sola celda.
            tipo, _, numero = datos["documento"].partition("-")
            return Afiliado(
                tipo_documento=tipo.strip(),
                numero_documento=numero.strip(),
                apellido_paterno=datos["apellido_paterno"],
                apellido_materno=datos["apellido_materno"],
                nombres=datos["nombres"],
                cuspp=datos["cuspp"],
                devengue_maximo=a_periodo(datos["devengue_maximo"]),
                motivo_pension=datos["motivo_pension"],
                ultimo_devengue=a_periodo(datos["ultimo_devengue"]),
                situacion=datos["situacion"],
                afp=AFP_POR_NOMBRE.get(datos["afp"].upper(), "desconocida"),
                tipo_comision=datos["tipo_comision"],
                porcentaje_comision=a_decimal(datos["porcentaje_comision"]),
            )
    return None


# ── Historial de aportes del afiliado ─────────────────────────────────────

@dataclass
class AporteMensual:
    periodo: str
    id_obligacion: str = ""
    tipo: str = ""              # C: cierta · P: presunta
    relacion_laboral: bool = True
    remuneracion: Decimal | None = None
    obligado_fondo: Decimal | None = None
    obligado_seguro: Decimal | None = None
    obligado_comision: Decimal | None = None
    declarado_fondo: Decimal | None = None
    pagado_fondo: Decimal | None = None
    declarado_no_pagado: Decimal | None = None

    @property
    def pagado(self) -> bool:
        """Pagado de verdad: hay importe pagado y nada declarado sin pagar."""
        return bool(self.pagado_fondo) and not self.declarado_no_pagado


def parsear_historial_aportes(cuerpo: str | dict) -> list[AporteMensual]:
    """El historial mes a mes que devuelve ``getSessionOpListJson``.

    Es el único endpoint con forma de API, pero está atado a la sesión: refleja
    el afiliado que se consultó antes. Quien lo llame debe haber fijado ese
    afiliado primero, o estará leyendo el historial de otro.
    """
    datos = json.loads(cuerpo) if isinstance(cuerpo, str) else cuerpo
    registros = datos.get("result") or []

    aportes: list[AporteMensual] = []
    for registro in registros:
        periodo = a_periodo(registro.get("devengue"))
        if not periodo:
            continue
        detalles = registro.get("listaObligacionPagoDetalle") or []
        detalle = detalles[0] if detalles else {}
        aportes.append(AporteMensual(
            periodo=periodo,
            id_obligacion=str(registro.get("idObligacionPago") or ""),
            tipo=registro.get("tipo") or "",
            relacion_laboral=registro.get("flagRelacionLaboral") == "S",
            remuneracion=a_decimal(detalle.get("remuneracion")),
            obligado_fondo=a_decimal(detalle.get("montoObligadoFondo")),
            obligado_seguro=a_decimal(detalle.get("montoObligadoSeguro")),
            obligado_comision=a_decimal(detalle.get("montoObligadoComision")),
            declarado_fondo=a_decimal(detalle.get("montoDeclaradoFondo")),
            pagado_fondo=a_decimal(detalle.get("montoPagadoFondo")),
            declarado_no_pagado=a_decimal(detalle.get("montoDeclaradoNoPagado")),
        ))
    return sorted(aportes, key=lambda a: a.periodo)


# ── Datos de la empresa ───────────────────────────────────────────────────

@dataclass
class DatosEmpresa:
    ruc: str = ""
    razon_social: str = ""
    telefono: str = ""
    direccion: str = ""
    departamento: str = ""
    provincia: str = ""
    distrito: str = ""
    representante: str = ""
    representante_documento: str = ""
    representante_cargo: str = ""
    representante_correo: str = ""
    representante_telefono: str = ""
    segunda_firma: bool = False


# Campo del formulario → atributo. Se leen del formulario de modificación, que
# es la única pantalla donde AFPnet publica estos datos; **solo se lee**: un
# POST ahí cambiaría los datos de la empresa en el sistema previsional.
CAMPOS_EMPRESA = {
    "EmpresaRuc": "ruc",
    "RazonSocial": "razon_social",
    "CelularEmpresa": "telefono",
    "RptaLegalDocNum": "representante_documento",
    "CargoLegal": "representante_cargo",
    "CorreoLegal": "representante_correo",
    "CelularRptaLegal": "representante_telefono",
}
PARTES_REPRESENTANTE = (
    "RptaLegalNombre1", "RptaLegalNombre2",
    "RptaLegalApePaterno", "RptaLegalApeMaterno",
)
PARTES_DIRECCION = ("NombreViaEmpl", "NumeroViaEmpl", "DepartamentoEmpl")
SELECTS_UBIGEO = {
    "UbigeoDepartamento": "departamento",
    "UbigeoProvincia": "provincia",
    "UbigeoDistrito": "distrito",
}


def parsear_datos_empresa(html: str) -> DatosEmpresa:
    """Los datos que AFPnet tiene de la empresa y de su representante legal."""
    sopa = BeautifulSoup(html, "html.parser")
    valores: dict[str, str] = {}
    for entrada in sopa.find_all("input"):
        nombre = entrada.get("name") or entrada.get("id")
        if nombre:
            valores[nombre] = (entrada.get("value") or "").strip()

    datos = DatosEmpresa()
    for campo, atributo in CAMPOS_EMPRESA.items():
        setattr(datos, atributo, valores.get(campo, ""))

    datos.representante = " ".join(
        valores.get(p, "") for p in PARTES_REPRESENTANTE
    ).split()
    datos.representante = " ".join(datos.representante)
    datos.direccion = " ".join(
        v for v in (valores.get(p, "") for p in PARTES_DIRECCION) if v
    )
    # ASP.NET emite un checkbox `value="true"` junto a un hidden `value="false"`.
    # Leer el `value` daría siempre «true», esté marcado o no: lo que informa es
    # el atributo `checked`.
    casilla = sopa.find("input", {"name": "BolSegundaFirma", "type": "checkbox"})
    datos.segunda_firma = bool(casilla and casilla.has_attr("checked"))

    for select in sopa.find_all("select"):
        nombre = select.get("name") or select.get("id")
        atributo = SELECTS_UBIGEO.get(nombre or "")
        if not atributo:
            continue
        elegida = select.find("option", selected=True)
        texto = _texto(elegida)
        # El portal deja «Seleccione» cuando no hay valor: no es un dato.
        if texto and texto.lower() != "seleccione":
            setattr(datos, atributo, texto)
    return datos
