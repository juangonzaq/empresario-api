"""Lo que un auditor de SUNAT vería en tus compras, antes de que lo vea él.

El módulo de exposición responde «¿a quién le compro que hoy está marcado?».
Esto responde otra pregunta, anterior y más incómoda: **¿qué compras mías
parecen falsas aunque el proveedor esté impecable?**. Una fiscalización por
operaciones no reales (art. 44 de la Ley del IGV) no empieza por el estado del
RUC, empieza por patrones: un proveedor que se inscribió hace dos meses, que
emitió siete facturas el mismo día, que se dio de baja justo después de
facturarte, o cuyas facturas te llegan todas correlativas porque eres su único
cliente.

Cada patrón es una *señal*, no un veredicto. Comprarle a un proveedor nuevo es
legítimo; que además te facture siete veces en un día y luego desaparezca, ya
es lo que un auditor llama «indicio razonable». Por eso las señales se suman en
un puntaje y se explican una a una: quien dirige la empresa tiene que poder
leerlas y decir «esta tiene explicación» o «esta no».

Sobre los importes: son **estimaciones** para dimensionar la contingencia, no
una liquidación. El IGV se extrae del total (18/118); la renta se calcula con
la tasa del régimen general (29,5 %) sobre la base imponible; la multa es la
del art. 178.1 del Código Tributario (50 % del tributo omitido), sin intereses
ni gradualidad. Un contador afinará; aquí se trata de saber si son S/ 900 o
S/ 90.000.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sunat_cpe.models import DocumentClass, ElectronicInvoice

from ..models import Supplier
from .actividad import compatibilidad, parsear_actividades, sectores_de
from .constants import CONDITION_FOUND, STATUS_ACTIVE
from .exposure import CERO, _igv_estimado
from .ssco import rucs_en_padron

# Tasa del régimen general. Una MYPE tributa 10 % sobre las primeras 15 UIT,
# pero en una fiscalización el reparo se calcula sobre la renta que dejó de
# pagarse al deducir un gasto que no era real, y ese gasto casi siempre cae
# en el tramo alto. Se etiqueta como estimación en todas partes.
TASA_RENTA = Decimal("0.295")
# Art. 178 num. 1 del Código Tributario: 50 % del tributo omitido.
TASA_MULTA = Decimal("0.50")

# Umbrales. Están juntos y con nombre para poder discutirlos: cada uno es un
# criterio, no una verdad.
FACTURAS_MISMO_DIA = 3          # desde aquí un día se considera «ráfaga»
RAFAGA_GRAVE = 5                # con estas en un día ya no hace falta más
DIAS_PROVEEDOR_NUEVO = 90       # facturó antes de cumplir tres meses
DIAS_PROVEEDOR_RECIEN_NACIDO = 30
DIAS_BAJA_TRAS_FACTURAR = 180   # cayó dentro de los seis meses de tu última compra
MINIMO_PARA_PATRON = 3          # patrones estadísticos con menos de esto no dicen nada
CORRELATIVAS_MINIMO = 5         # facturas para juzgar correlatividad
CORRELATIVAS_COBERTURA = Decimal("0.8")  # fracción de la numeración que te llegó
REDONDOS_FRACCION = Decimal("0.6")       # fracción de facturas con monto redondo
CIERRE_FRACCION = Decimal("0.5")         # fracción del gasto en nov-dic

# ── Capacidad operativa (los criterios con los que SUNAT arma el padrón SSCO) ──
# Se miden con lo único público que hay: la sección «Cantidad de trabajadores»
# (PLAME) y «Establecimientos anexos» de la consulta RUC del proveedor. Sin su
# ficha capturada, estas señales no opinan. Capacidad financiera y logística
# no tienen fuente pública; quedan fuera a propósito.
UIT = Decimal("5500")                     # 2026
MESES_VENTANA_VOLUMEN = 12                # lo que te facturó en su último año
VOLUMEN_SIN_PERSONAL_UIT = 10             # ≥ S/ 55 000 con 0 personas
VOLUMEN_POR_PERSONA_UIT = 25              # ≥ S/ 137 500 por persona y año
VOLUMEN_POR_PERSONA_GRAVE = 2             # el doble ya es alta
# Rubros que no se ejercen sin un local, almacén o taller. Una consultora sin
# anexos es lo normal; una fábrica de muebles sin ninguno, no.
SECTORES_CON_LOCAL = frozenset({
    "manufactura", "comercio_general", "textil", "alimentos", "vehiculos",
    "mineria", "agro", "hoteleria", "construccion",
})
MESES_CIERRE = (11, 12)

# «critica» es el padrón SSCO: sola ya deja al proveedor en nivel alto.
PUNTOS = {"critica": 5, "alta": 3, "media": 2, "baja": 1}


@dataclass
class Senal:
    """Un indicio, explicado como lo diría alguien que revisa compras."""

    clave: str
    gravedad: str  # critica | alta | media | baja
    titulo: str
    detalle: str
    comprobantes: int = 0
    importe: Decimal = CERO


@dataclass
class AnalisisProveedor:
    ruc: str
    proveedor: str
    supplier_id: str | None
    estado: str
    condicion: str
    registrado_el: date | None
    inicio_actividades: date | None
    actividad_principal: str
    comprobantes: int
    total: Decimal
    primera_compra: date | None
    ultima_compra: date | None
    # Lo que su ficha RUC dice de su capacidad: None = ficha no capturada.
    trabajadores: int | None = None
    anexos: int | None = None
    senales: list[Senal] = field(default_factory=list)

    @property
    def puntaje(self) -> int:
        return sum(PUNTOS[s.gravedad] for s in self.senales)

    @property
    def nivel(self) -> str:
        if self.puntaje >= 5:
            return "alto"
        if self.puntaje >= 2:
            return "medio"
        if self.puntaje >= 1:
            return "bajo"
        return "sin_senales"

    @property
    def igv_estimado(self) -> Decimal:
        return _igv_estimado(self.total)

    @property
    def base_imponible(self) -> Decimal:
        return (self.total - self.igv_estimado).quantize(Decimal("0.01"))

    @property
    def renta_estimada(self) -> Decimal:
        return (self.base_imponible * TASA_RENTA).quantize(Decimal("0.01"))


@dataclass
class Fiscalizacion:
    """La contingencia agregada: lo que se discutiría y lo que costaría."""

    proveedores_analizados: int = 0
    proveedores_observados: int = 0
    comprobantes_observados: int = 0
    total_observado: Decimal = CERO
    igv_en_riesgo: Decimal = CERO
    renta_en_riesgo: Decimal = CERO
    multa_estimada: Decimal = CERO
    proveedores: list[AnalisisProveedor] = field(default_factory=list)
    por_senal: dict[str, int] = field(default_factory=dict)

    @property
    def contingencia_total(self) -> Decimal:
        return self.igv_en_riesgo + self.renta_en_riesgo + self.multa_estimada


@dataclass
class _Factura:
    fecha: date | None
    total: Decimal
    serie: str
    numero: str


@dataclass
class _Capacidad:
    """Personal y locales del proveedor según su ficha RUC, cotejados con los
    meses en que te facturó."""

    # Máximo de trabajadores + prestadores en los periodos cotejados. 0 = SUNAT
    # no registra a nadie; None = la sección de planilla no se capturó.
    personas: int | None
    anexos: int | None
    periodos: list[str] = field(default_factory=list)
    actividades: str = ""


def _capacidad(ficha, facturas: list[_Factura]) -> _Capacidad | None:
    """Lee la ficha (con ``headcounts`` y ``sections`` precargados).

    Se cotejan los periodos PLAME de los meses en que facturó; si la ficha no
    cubre esos meses, se usan sus últimos doce periodos declarados. Una
    sección de planilla capturada sin filas es un «no registra trabajadores»
    de SUNAT, no un dato faltante.
    """
    if ficha is None:
        return None
    filas = {
        h.period: (h.workers or 0) + (h.service_providers or 0)
        for h in ficha.headcounts.all()
    }
    meses = {f"{f.fecha:%Y-%m}" for f in facturas if f.fecha}
    cotejados = sorted(p for p in filas if p in meses)
    if not cotejados:
        cotejados = sorted(filas)[-MESES_VENTANA_VOLUMEN:]

    planilla_capturada = any(
        sec.key == "workers" and not sec.error for sec in ficha.sections.all()
    )
    if cotejados:
        personas: int | None = max(filas[p] for p in cotejados)
    elif planilla_capturada:
        personas = 0
    else:
        personas = None
    return _Capacidad(
        personas=personas, anexos=ficha.branch_count, periodos=cotejados,
        actividades=ficha.economic_activities or "",
    )


def _rango(periodos: list[str]) -> str:
    if not periodos:
        return "sus últimos periodos declarados"
    if len(periodos) == 1:
        return periodos[0]
    return f"{periodos[0]} a {periodos[-1]}"


def _ultimo_anio(facturas: list[_Factura]) -> list[_Factura]:
    fechas = [f.fecha for f in facturas if f.fecha]
    if not fechas:
        return facturas
    ultima = max(fechas)
    desde = date(ultima.year - 1, ultima.month, 1)
    return [f for f in facturas if f.fecha and f.fecha > desde]


def _facturas_por_emisor(account_ruc: str) -> dict[str, list[_Factura]]:
    """Todas las facturas recibidas, agrupadas por emisor, en una consulta.

    Se traen columnas sueltas y no modelos: una empresa mediana acumula decenas
    de miles de comprobantes y aquí solo hacen falta cuatro campos de cada uno.
    Las notas de crédito quedan fuera del patrón —lo que se analiza es cómo
    factura el proveedor— y se netean solo del importe, como en exposición.
    """
    filas = (
        ElectronicInvoice.objects.for_account(account_ruc)
        .received()
        .filter(
            is_cancelled=False,
            document_class__in=[DocumentClass.INVOICE, DocumentClass.DEBIT_NOTE],
        )
        .values_list("issuer_ruc", "issue_date", "total_amount", "series", "number")
    )
    por_emisor: dict[str, list[_Factura]] = defaultdict(list)
    for ruc, fecha, total, serie, numero in filas:
        ruc = (ruc or "").strip()
        if ruc and ruc != account_ruc:
            por_emisor[ruc].append(_Factura(fecha, total or CERO, serie, numero))
    return por_emisor


# ── Las señales ──────────────────────────────────────────────────────────────


def _mismo_dia(facturas: list[_Factura]) -> Senal | None:
    """Varias facturas el mismo día del mismo proveedor.

    Es el patrón más visto en facturas de favor: se «fabrica» el gasto de
    golpe, muchas veces a fin de mes o de año. Un proveedor real factura al
    ritmo en que entrega.
    """
    por_dia = Counter(f.fecha for f in facturas if f.fecha)
    rafagas = {dia: n for dia, n in por_dia.items() if n >= FACTURAS_MISMO_DIA}
    if not rafagas:
        return None
    peor_dia, peor_n = max(rafagas.items(), key=lambda par: par[1])
    en_rafagas = sum(rafagas.values())
    importe = sum((f.total for f in facturas if f.fecha in rafagas), CERO)
    grave = peor_n >= RAFAGA_GRAVE or en_rafagas * 2 >= len(facturas)
    dias = len(rafagas)
    return Senal(
        clave="mismo_dia",
        gravedad="alta" if grave else "media",
        titulo="Varias facturas el mismo día",
        detalle=(
            f"{en_rafagas} de sus {len(facturas)} facturas se emitieron en "
            f"{dias} {'día' if dias == 1 else 'días'}; el peor fue el "
            f"{peor_dia:%d/%m/%Y}, con {peor_n}. Un proveedor real factura al "
            f"ritmo en que entrega."
        ),
        comprobantes=en_rafagas,
        importe=importe,
    )


def _proveedor_reciente(
    supplier: Supplier | None, primera: date | None, facturas: list[_Factura],
) -> Senal | None:
    """Te facturó recién inscrito en SUNAT.

    Empresas creadas para emitir facturas y desaparecer: inscripción, dos o
    tres meses de actividad y baja. La fecha de inicio de actividades es la
    que mira el auditor.
    """
    if supplier is None or primera is None:
        return None
    nacimiento = supplier.started_activities_on or supplier.registered_on
    if nacimiento is None:
        return None
    dias = (primera - nacimiento).days
    if dias > DIAS_PROVEEDOR_NUEVO:
        return None
    importe = sum(
        (f.total for f in facturas
         if f.fecha and (f.fecha - nacimiento).days <= DIAS_PROVEEDOR_NUEVO),
        CERO,
    )
    cuantas = sum(
        1 for f in facturas
        if f.fecha and (f.fecha - nacimiento).days <= DIAS_PROVEEDOR_NUEVO
    )
    return Senal(
        clave="proveedor_reciente",
        gravedad="alta" if dias <= DIAS_PROVEEDOR_RECIEN_NACIDO else "media",
        titulo="Te facturó recién inscrito",
        detalle=(
            f"Inició actividades el {nacimiento:%d/%m/%Y} y su primera factura "
            f"a tu empresa es del {primera:%d/%m/%Y}: {max(dias, 0)} días después. "
            f"{cuantas} facturas caen en sus primeros {DIAS_PROVEEDOR_NUEVO} días."
        ),
        comprobantes=cuantas,
        importe=importe,
    )


def _baja_tras_facturar(
    supplier: Supplier | None, ultima: date | None, facturas: list[_Factura],
) -> Senal | None:
    """Dejó de estar activo poco después de tu última compra.

    Es la firma de la empresa de fachada: alcanza un tope de facturación y se
    suspende o se da de baja. Si además ya estaba marcado cuando te facturó,
    el módulo de riesgo fiscal lo recoge; aquí lo que se mira es la secuencia.
    """
    if supplier is None or ultima is None or not supplier.status:
        return None
    if supplier.status.upper() == STATUS_ACTIVE:
        return None
    # Sin fecha de cambio conocida, se toma la última consulta: el proveedor
    # cayó en algún momento antes de ella.
    cuando = supplier.last_changed_at or supplier.last_checked_at
    fecha_cambio = cuando.date() if cuando else None
    if fecha_cambio is not None and (fecha_cambio - ultima).days > DIAS_BAJA_TRAS_FACTURAR:
        return None
    return Senal(
        clave="baja_tras_facturar",
        gravedad="alta",
        titulo="Dejó de estar activo tras facturarte",
        detalle=(
            f"Hoy figura como {supplier.status}"
            f"{' · ' + supplier.condition if supplier.condition else ''}. "
            f"Tu última compra es del {ultima:%d/%m/%Y}"
            + (
                f" y el cambio se detectó el {fecha_cambio:%d/%m/%Y}."
                if fecha_cambio else "."
            )
            + " Un proveedor que factura y desaparece es lo primero que se revisa."
        ),
        comprobantes=len(facturas),
        importe=sum((f.total for f in facturas), CERO),
    )


def _ssco(ruc: str, padron: dict, facturas: list[_Factura]) -> Senal | None:
    """Está en el padrón de Sujetos Sin Capacidad Operativa.

    Es lo peor que puede pasarle a una compra: SUNAT ya resolvió, con
    resolución firme, que ese contribuyente no tenía cómo hacer lo que
    facturó. Sus comprobantes no dan crédito fiscal ni gasto, sin prueba en
    contrario que valga (D. Leg. 1532). No hace falta ninguna otra señal.
    """
    sujeto = padron.get(ruc)
    if sujeto is None:
        return None
    firme = f" firme desde el {sujeto.fecha_firme:%d/%m/%Y}" if sujeto.fecha_firme else ""
    return Senal(
        clave="ssco",
        gravedad="critica",
        titulo="Sujeto Sin Capacidad Operativa",
        detalle=(
            f"Figura en el padrón SSCO de SUNAT ({sujeto.resolucion or 'resolución'}"
            f"{firme}). Sus facturas no dan crédito fiscal ni gasto deducible y no "
            f"admiten prueba en contrario."
        ),
        comprobantes=len(facturas),
        importe=sum((f.total for f in facturas), CERO),
    )


def _no_habido(supplier: Supplier | None, facturas: list[_Factura]) -> Senal | None:
    """SUNAT no lo encuentra en su domicilio fiscal.

    NO HABIDO es la condición que más pesa: el art. 44 de la Ley del IGV
    presume que las operaciones con un no habido no son reales salvo prueba en
    contrario, y el crédito fiscal de sus facturas cae de entrada. El texto de
    SUNAT lo dice más suave —«deberá declarar el nuevo domicilio»—, pero para
    quien le compra el efecto es ese.
    """
    if supplier is None or not supplier.condition:
        return None
    if supplier.condition.upper() == CONDITION_FOUND:
        return None
    return Senal(
        clave="no_habido",
        gravedad="alta",
        titulo=f"Condición {supplier.condition.title()}",
        detalle=(
            f"SUNAT no lo ubica en su domicilio fiscal (condición "
            f"{supplier.condition}). Las compras a un no habido se presumen no "
            f"reales y el crédito fiscal de sus facturas se pierde salvo que "
            f"pruebes que la operación existió."
        ),
        comprobantes=len(facturas),
        importe=sum((f.total for f in facturas), CERO),
    )


def _actividad_ajena(
    supplier: Supplier | None, actividad_empresa: str, facturas: list[_Factura],
) -> Senal | None:
    """Lo que vende no es un insumo evidente de lo que haces.

    Una empresa de servicios con muchas facturas de una ferretería tiene que
    explicar qué compró. Se compara por sector, no por código CIIU, porque
    SUNAT mezcla revisiones del CIIU y el mismo número cambia de significado.
    """
    if supplier is None:
        return None
    cruce = compatibilidad(actividad_empresa, supplier.economic_activities)
    if cruce.compatible:
        return None
    return Senal(
        clave="actividad_ajena",
        gravedad="media",
        titulo="Su actividad no encaja con la tuya",
        detalle=cruce.motivo,
        comprobantes=len(facturas),
        importe=sum((f.total for f in facturas), CERO),
    )


def _correlativas(facturas: list[_Factura]) -> Senal | None:
    """Sus facturas te llegan casi correlativas: eres su único cliente.

    Si de la serie F001 tienes la 12, 13, 14, 15 y 16, el proveedor no le
    facturó a nadie más en medio. Un negocio con un solo cliente puede
    existir, pero es lo que un auditor pregunta primero.
    """
    por_serie: dict[str, list[int]] = defaultdict(list)
    for f in facturas:
        if f.numero.isdigit():
            por_serie[f.serie].append(int(f.numero))
    peor: tuple[str, int, int] | None = None
    for serie, numeros in por_serie.items():
        if len(numeros) < CORRELATIVAS_MINIMO:
            continue
        distintos = sorted(set(numeros))
        rango = distintos[-1] - distintos[0] + 1
        if Decimal(len(distintos)) / Decimal(rango) >= CORRELATIVAS_COBERTURA:
            if peor is None or len(distintos) > peor[1]:
                peor = (serie, len(distintos), rango)
    if peor is None:
        return None
    serie, cuantas, rango = peor
    return Senal(
        clave="correlativas",
        gravedad="media",
        titulo="Numeración casi correlativa",
        detalle=(
            f"De la serie {serie} tienes {cuantas} de {rango} números "
            f"consecutivos: entre una factura tuya y la siguiente casi no le "
            f"facturó a nadie más."
        ),
        comprobantes=cuantas,
        importe=sum((f.total for f in facturas if f.serie == serie), CERO),
    )


def _montos_redondos(facturas: list[_Factura]) -> Senal | None:
    """Importes redondos en casi todas las facturas.

    Un servicio real rara vez sale en S/ 5.000,00 exactos con IGV incluido.
    Es una señal débil sola; pesa cuando acompaña a otras.
    """
    if len(facturas) < MINIMO_PARA_PATRON:
        return None
    redondas = [f for f in facturas if f.total and f.total % 100 == 0]
    if Decimal(len(redondas)) / Decimal(len(facturas)) < REDONDOS_FRACCION:
        return None
    return Senal(
        clave="montos_redondos",
        gravedad="baja",
        titulo="Importes redondos",
        detalle=(
            f"{len(redondas)} de {len(facturas)} facturas son múltiplos exactos "
            f"de S/ 100 con IGV incluido. Un servicio real rara vez cuadra así."
        ),
        comprobantes=len(redondas),
        importe=sum((f.total for f in redondas), CERO),
    )


def _cierre_ejercicio(facturas: list[_Factura]) -> Senal | None:
    """El gasto se concentra en noviembre y diciembre.

    Es cuando se sabe cuánta renta va a salir y aparecen las compras que la
    rebajan. Se mira por importe, no por número de facturas.
    """
    if len(facturas) < MINIMO_PARA_PATRON:
        return None
    total = sum((f.total for f in facturas), CERO)
    if total <= CERO:
        return None
    en_cierre = [f for f in facturas if f.fecha and f.fecha.month in MESES_CIERRE]
    importe = sum((f.total for f in en_cierre), CERO)
    if importe / total < CIERRE_FRACCION:
        return None
    return Senal(
        clave="cierre_ejercicio",
        gravedad="media",
        titulo="Compras concentradas al cierre del año",
        detalle=(
            f"El {int(importe * 100 / total)} % de lo que le compraste "
            f"({len(en_cierre)} facturas) es de noviembre y diciembre, cuando ya "
            f"se sabe cuánta renta va a salir."
        ),
        comprobantes=len(en_cierre),
        importe=importe,
    )


def _sin_personal(cap: _Capacidad | None, facturas: list[_Factura]) -> Senal | None:
    """No declara a nadie en PLAME mientras te facturaba.

    Es el primer criterio con el que SUNAT arma el padrón SSCO: quien facturó
    tuvo que hacer el trabajo alguien. Se cuentan trabajadores y prestadores
    de servicio (recibos por honorarios); un titular que trabaja solo no
    aparece en PLAME, así que la señal pesa más cuanto más facturó.
    """
    if cap is None or cap.personas is None or cap.personas > 0:
        return None
    total = sum((f.total for f in facturas), CERO)
    return Senal(
        clave="sin_personal",
        gravedad="alta",
        titulo="Sin trabajadores declarados",
        detalle=(
            f"En su ficha RUC no figura ningún trabajador ni prestador de "
            f"servicios en PLAME ({_rango(cap.periodos)}). Te facturó "
            f"S/ {total:,.0f} y alguien tuvo que hacer ese trabajo: es el primer "
            f"criterio que SUNAT mira para declarar a un proveedor sin capacidad "
            f"operativa."
        ),
        comprobantes=len(facturas),
        importe=total,
    )


def _sin_local(cap: _Capacidad | None, facturas: list[_Factura]) -> Senal | None:
    """Rubro que exige local y solo tiene declarado el domicilio fiscal.

    Los establecimientos anexos son la única huella pública de
    infraestructura. Solo opina cuando la actividad es de las que no se
    ejercen sin almacén, taller o tienda.
    """
    if cap is None or cap.anexos is None or cap.anexos > 0:
        return None
    actividades = parsear_actividades(cap.actividades)
    sectores = frozenset().union(*(a.sectores for a in actividades)) if actividades else frozenset()
    if not sectores & SECTORES_CON_LOCAL:
        return None
    return Senal(
        clave="sin_local",
        gravedad="media",
        titulo="Sin local, almacén ni taller",
        detalle=(
            f"Se dedica a «{actividades[0].descripcion.lower()}» y en SUNAT solo "
            f"tiene su domicilio fiscal: ningún establecimiento anexo. Un rubro "
            f"así sin dónde producir, almacenar o atender es lo que un auditor "
            f"pide explicar."
        ),
        comprobantes=len(facturas),
        importe=sum((f.total for f in facturas), CERO),
    )


def _volumen_desproporcionado(
    cap: _Capacidad | None, facturas: list[_Factura],
) -> Senal | None:
    """Te facturó mucho más de lo que su tamaño permite.

    Se mira solo lo que te facturó a ti en su último año, que ya es un piso
    de su facturación real. Con cero personas basta con superar unas UIT; con
    personal, se mide por persona.
    """
    if cap is None or cap.personas is None:
        return None
    ultimo_anio = _ultimo_anio(facturas)
    total = sum((f.total for f in ultimo_anio), CERO)
    if cap.personas == 0:
        if total < UIT * VOLUMEN_SIN_PERSONAL_UIT:
            return None
        gravedad = "alta"
        comparacion = (
            f"sin declarar a nadie en planilla (el umbral es "
            f"{VOLUMEN_SIN_PERSONAL_UIT} UIT, S/ {UIT * VOLUMEN_SIN_PERSONAL_UIT:,.0f})"
        )
    else:
        por_persona = total / cap.personas
        umbral = UIT * VOLUMEN_POR_PERSONA_UIT
        if por_persona < umbral:
            return None
        gravedad = "alta" if por_persona >= umbral * VOLUMEN_POR_PERSONA_GRAVE else "media"
        comparacion = (
            f"con {cap.personas} {'persona' if cap.personas == 1 else 'personas'} "
            f"declaradas: S/ {por_persona:,.0f} por cabeza al año, más de "
            f"{VOLUMEN_POR_PERSONA_UIT} UIT"
        )
    if cap.anexos == 0:
        comparacion += " y sin ningún local declarado"
    return Senal(
        clave="volumen_desproporcionado",
        gravedad=gravedad,
        titulo="Factura más de lo que su tamaño permite",
        detalle=(
            f"En su último año te facturó S/ {total:,.0f} {comparacion}. Una "
            f"facturación grande con una estructura mínima es el patrón típico "
            f"del proveedor de fachada."
        ),
        comprobantes=len(ultimo_anio),
        importe=total,
    )


def analizar(
    ruc: str,
    facturas: list[_Factura],
    supplier: Supplier | None,
    nombre: str = "",
    actividad_empresa: str = "",
    padron_ssco: dict | None = None,
    ficha=None,
) -> AnalisisProveedor:
    fechas = sorted(f.fecha for f in facturas if f.fecha)
    primera = fechas[0] if fechas else None
    ultima = fechas[-1] if fechas else None
    analisis = AnalisisProveedor(
        ruc=ruc,
        proveedor=supplier.display_name if supplier else (nombre or ruc),
        supplier_id=str(supplier.pk) if supplier else None,
        estado=supplier.status if supplier else "",
        condicion=supplier.condition if supplier else "",
        registrado_el=supplier.registered_on if supplier else None,
        inicio_actividades=supplier.started_activities_on if supplier else None,
        actividad_principal=_principal(supplier),
        comprobantes=len(facturas),
        total=sum((f.total for f in facturas), CERO),
        primera_compra=primera,
        ultima_compra=ultima,
    )
    cap = _capacidad(ficha, facturas)
    if cap is not None:
        analisis.trabajadores = cap.personas
        analisis.anexos = cap.anexos
    candidatas = (
        _ssco(ruc, padron_ssco or {}, facturas),
        _no_habido(supplier, facturas),
        _baja_tras_facturar(supplier, ultima, facturas),
        _sin_personal(cap, facturas),
        _volumen_desproporcionado(cap, facturas),
        _sin_local(cap, facturas),
        _mismo_dia(facturas),
        _actividad_ajena(supplier, actividad_empresa, facturas),
        _proveedor_reciente(supplier, primera, facturas),
        _correlativas(facturas),
        _cierre_ejercicio(facturas),
        _montos_redondos(facturas),
    )
    analisis.senales = [s for s in candidatas if s is not None]
    return analisis


def _principal(supplier: Supplier | None) -> str:
    if supplier is None:
        return ""
    actividades = parsear_actividades(supplier.economic_activities)
    return actividades[0].descripcion if actividades else ""


def actividad_de_la_empresa(account_ruc: str) -> str:
    """Las actividades de la empresa compradora, según su última ficha RUC.

    Salen del perfil RUC propio que captura la sincronización. Sin ficha
    todavía, la comparación de actividades simplemente no opina.
    """
    from ruc_profile.models import RucSnapshot

    ficha = RucSnapshot.objects.for_ruc(account_ruc).order_by("-captured_on").first()
    return ficha.economic_activities if ficha else ""


def fichas_de_proveedores(rucs) -> dict:
    """La última ficha RUC capturada de cada emisor, con planilla y secciones.

    Las captura ``ruc_profile.capture`` cada mes para empresas, vigilados y
    quien haya facturado en el último año. Sin ficha, las señales de
    capacidad operativa simplemente no opinan.
    """
    from ruc_profile.models import RucSnapshot

    rucs = [r for r in rucs if r]
    if not rucs:
        return {}
    fichas = (
        RucSnapshot.objects.filter(ruc__in=rucs, succeeded=True)
        .latest_per_ruc()
        .prefetch_related("headcounts", "sections")
    )
    return {f.ruc: f for f in fichas}


def analizar_proveedor(supplier: Supplier) -> AnalisisProveedor:
    """Las señales de un solo proveedor, para su ficha."""
    facturas = _facturas_por_emisor(supplier.account_ruc).get(supplier.ruc, [])
    return analizar(
        supplier.ruc, facturas, supplier,
        actividad_empresa=actividad_de_la_empresa(supplier.account_ruc),
        padron_ssco=rucs_en_padron([supplier.ruc]),
        ficha=fichas_de_proveedores([supplier.ruc]).get(supplier.ruc),
    )


def _nombres(account_ruc: str, rucs: set[str]) -> dict[str, str]:
    filas = (
        ElectronicInvoice.objects.for_account(account_ruc)
        .received()
        .filter(issuer_ruc__in=rucs)
        .exclude(issuer_name="")
        .values_list("issuer_ruc", "issuer_name")
        .distinct()
    )
    return {ruc: nombre for ruc, nombre in filas}


def simular_fiscalizacion(account_ruc: str) -> Fiscalizacion:
    """Cruza todas las compras con la cartera y suma lo que se discutiría.

    Se analizan **todos** los emisores, registrados o no: al auditor le da
    igual si vigilabas al proveedor. Sin ficha en la cartera solo se pueden
    ver los patrones de facturación; con ella, también la fecha de inscripción
    y el estado.
    """
    por_emisor = _facturas_por_emisor(account_ruc)
    cartera = {
        s.ruc: s for s in Supplier.objects.filter(
            account_ruc=account_ruc, ruc__in=por_emisor.keys(),
        )
    }
    nombres = _nombres(account_ruc, set(por_emisor) - set(cartera))
    actividad_empresa = actividad_de_la_empresa(account_ruc)
    padron_ssco = rucs_en_padron(por_emisor.keys())
    fichas = fichas_de_proveedores(por_emisor.keys())

    resultado = Fiscalizacion(proveedores_analizados=len(por_emisor))
    por_senal: Counter[str] = Counter()
    for ruc, facturas in por_emisor.items():
        analisis = analizar(
            ruc, facturas, cartera.get(ruc), nombres.get(ruc, ""),
            actividad_empresa=actividad_empresa, padron_ssco=padron_ssco,
            ficha=fichas.get(ruc),
        )
        if not analisis.senales:
            continue
        resultado.proveedores.append(analisis)
        resultado.proveedores_observados += 1
        resultado.comprobantes_observados += analisis.comprobantes
        resultado.total_observado += analisis.total
        resultado.igv_en_riesgo += analisis.igv_estimado
        resultado.renta_en_riesgo += analisis.renta_estimada
        por_senal.update(s.clave for s in analisis.senales)

    resultado.multa_estimada = (
        (resultado.igv_en_riesgo + resultado.renta_en_riesgo) * TASA_MULTA
    ).quantize(Decimal("0.01"))
    resultado.por_senal = dict(por_senal)
    resultado.proveedores.sort(key=lambda a: (a.puntaje, a.total), reverse=True)
    return resultado


__all__ = [
    "AnalisisProveedor",
    "Fiscalizacion",
    "Senal",
    "analizar_proveedor",
    "simular_fiscalizacion",
]
