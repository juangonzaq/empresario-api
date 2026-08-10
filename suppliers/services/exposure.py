"""Cuánto dinero hay detrás de cada proveedor, y cuánto está en riesgo.

La cartera de proveedores por sí sola no dice gran cosa: saber que uno está
NO HABIDO no ayuda a decidir si no se sabe cuánto se le compró. Aquí se cruza
el registro de proveedores con los comprobantes recibidos (CPE) para responder
las dos preguntas que se hace quien dirige la empresa:

* ¿a quién le compro de verdad, y por cuánto? — sirve para poblar la cartera
  sin teclear RUC uno a uno, y para ordenar el riesgo por dinero en vez de por
  orden alfabético.
* ¿qué facturas me pueden costar el crédito fiscal? — comprar a un proveedor
  NO HABIDO o dado de baja pone en discusión el IGV de esas facturas ante una
  fiscalización.

Sobre el IGV: los comprobantes que guarda SUNAT en la consulta CPE traen el
importe total, no el desglose. El IGV que se reporta aquí es una **estimación**
sacando el 18% incluido del total, y se llama así en todas partes. Sirve para
dimensionar («son 900 soles o son 90.000»), no para declarar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Count, Max, Q, Sum

from sunat_cpe.models import DocumentClass, ElectronicInvoice

from ..models import Supplier, SupplierCheck

# El IGV va incluido en el total, así que se extrae, no se añade.
IGV_INCLUIDO = Decimal("18") / Decimal("118")

CERO = Decimal("0.00")


def _igv_estimado(total: Decimal | None) -> Decimal:
    if not total:
        return CERO
    return (total * IGV_INCLUIDO).quantize(Decimal("0.01"))


@dataclass
class CompraPorProveedor:
    """Lo comprado a un RUC, según los comprobantes recibidos."""

    ruc: str
    business_name: str = ""
    comprobantes: int = 0
    total: Decimal = CERO
    ultima_compra: date | None = None

    @property
    def igv_estimado(self) -> Decimal:
        return _igv_estimado(self.total)


def compras_por_proveedor(account_ruc: str) -> dict[str, CompraPorProveedor]:
    """Totales de compra por emisor, netos de notas de crédito.

    Las notas de crédito se restan: una factura anulada por una NC no es
    exposición real, y contarla inflaría el riesgo justo donde el usuario
    necesita confiar en la cifra.
    """
    base = (
        ElectronicInvoice.objects.for_account(account_ruc)
        .received()
        .filter(is_cancelled=False)
    )
    cargos = Q(document_class__in=[DocumentClass.INVOICE, DocumentClass.DEBIT_NOTE])
    abonos = Q(document_class=DocumentClass.CREDIT_NOTE)

    filas = (
        base.values("issuer_ruc")
        .annotate(
            cargado=Sum("total_amount", filter=cargos),
            abonado=Sum("total_amount", filter=abonos),
            comprobantes=Count("id", filter=cargos),
            ultima=Max("issue_date"),
            nombre=Max("issuer_name"),
        )
        .order_by()
    )

    resultado: dict[str, CompraPorProveedor] = {}
    for fila in filas:
        ruc = (fila["issuer_ruc"] or "").strip()
        if not ruc:
            continue
        total = (fila["cargado"] or CERO) - (fila["abonado"] or CERO)
        resultado[ruc] = CompraPorProveedor(
            ruc=ruc,
            business_name=fila["nombre"] or "",
            comprobantes=fila["comprobantes"] or 0,
            total=max(total, CERO),
            ultima_compra=fila["ultima"],
        )
    return resultado


def proveedores_por_descubrir(account_ruc: str) -> list[CompraPorProveedor]:
    """Emisores a los que se les compra y que aún no están en la cartera.

    Es la vía razonable de poblar el registro: nadie teclea doscientos RUC a
    mano, pero todo el mundo quiere vigilar a quien le factura. Se devuelven
    ordenados por lo que pesan en la cuenta.
    """
    ya_registrados = set(
        Supplier.objects.filter(account_ruc=account_ruc).values_list("ruc", flat=True)
    )
    pendientes = [
        compra
        for ruc, compra in compras_por_proveedor(account_ruc).items()
        if ruc not in ya_registrados and ruc != account_ruc
    ]
    return sorted(pendientes, key=lambda c: c.total, reverse=True)


# Cuántos proveedores se incorporan solos en la primera carga. Cada uno es una
# consulta a SUNAT dentro del mismo paso, así que el tope evita que una empresa
# con cientos de emisores convierta su primera sincronización en una hora de
# espera. El resto queda propuesto en pantalla para incorporarlo cuando quiera.
TOPE_ALTA_INICIAL = 50


def incorporar_desde_compras(account_ruc: str, tope: int = TOPE_ALTA_INICIAL) -> int:
    """Da de alta a los proveedores con más peso que aún no están vigilados.

    Sirve para que la primera sincronización deje algo utilizable: un registro
    vacío no avisa de nada, y pedirle a quien acaba de entrar que teclee sus
    proveedores uno a uno es pedirle que no lo use. Se empieza por los que más
    facturan, que son los que más crédito fiscal ponen en juego.

    Quedan sin consultar a propósito: el paso de sincronización los revisa a
    continuación, en la misma corrida.
    """
    pendientes = proveedores_por_descubrir(account_ruc)[:tope]
    if not pendientes:
        return 0

    Supplier.objects.bulk_create(
        [
            Supplier(
                account_ruc=account_ruc,
                ruc=compra.ruc,
                business_name=compra.business_name,
                is_tracked=True,
            )
            for compra in pendientes
        ],
        ignore_conflicts=True,
    )
    return len(pendientes)


def _estado_en_fecha(supplier: Supplier, cuando: date) -> SupplierCheck | None:
    """La consulta más cercana anterior o igual a esa fecha.

    Solo hay historial desde que el proveedor entró en vigilancia, así que para
    compras anteriores esto devuelve ``None``. Se distingue a propósito de «no
    tenía problema»: afirmar que estaba bien sin haberlo mirado sería inventar.
    """
    return (
        supplier.checks.filter(checked_on__lte=cuando, succeeded=True)
        .order_by("-checked_on")
        .first()
    )


@dataclass
class FacturaEnRiesgo:
    ruc_proveedor: str
    proveedor: str
    comprobante: str
    fecha: date | None
    total: Decimal
    igv_estimado: Decimal
    estado_hoy: str
    condicion_hoy: str
    estado_en_la_fecha: str = ""
    condicion_en_la_fecha: str = ""
    confirmado_en_la_fecha: bool = False


@dataclass
class ResumenRiesgo:
    """Lo que está en juego, en soles, por comprar a proveedores marcados."""

    proveedores: int = 0
    comprobantes: int = 0
    total: Decimal = CERO
    igv_estimado: Decimal = CERO
    confirmados: int = 0


def proveedores_marcados(account_ruc: str) -> dict[str, Supplier]:
    return {
        s.ruc: s
        for s in Supplier.objects.filter(account_ruc=account_ruc, has_issue=True)
    }


def comprobantes_en_riesgo(account_ruc: str):
    """Los comprobantes recibidos de proveedores hoy marcados por SUNAT.

    Se devuelve el queryset sin evaluar para que la vista lo pagine: la lista
    puede tener miles de filas y traerlas todas para enseñar veinte no tiene
    sentido.
    """
    marcados = proveedores_marcados(account_ruc)
    if not marcados:
        return ElectronicInvoice.objects.none()
    return (
        ElectronicInvoice.objects.for_account(account_ruc)
        .received()
        .filter(
            is_cancelled=False,
            issuer_ruc__in=marcados.keys(),
            document_class__in=[DocumentClass.INVOICE, DocumentClass.DEBIT_NOTE],
        )
        .order_by("-issue_date", "-number")
    )


def resumen_riesgo(account_ruc: str) -> ResumenRiesgo:
    """Los totales, sobre el conjunto completo y no sobre la página visible.

    Un total que cambia al pasar de página no es un total; y aquí la cifra es
    justamente lo que se mira para decidir, así que se calcula agregando en la
    base en lugar de recorriendo filas.
    """
    marcados = proveedores_marcados(account_ruc)
    if not marcados:
        return ResumenRiesgo()

    comprobantes = comprobantes_en_riesgo(account_ruc)
    agregado = comprobantes.aggregate(
        n=Count("id"), suma=Sum("total_amount"),
        emisores=Count("issuer_ruc", distinct=True),
    )
    total = agregado["suma"] or CERO

    # «Confirmado» necesita mirar el historial de cada proveedor, pero eso son
    # tantas consultas como proveedores marcados —no como comprobantes—, así
    # que sigue siendo barato.
    confirmados = 0
    for ruc, supplier in marcados.items():
        for fecha in comprobantes.filter(issuer_ruc=ruc).values_list(
            "issue_date", flat=True
        ):
            if fecha and (previa := _estado_en_fecha(supplier, fecha)):
                confirmados += int(previa.has_issue)

    return ResumenRiesgo(
        proveedores=agregado["emisores"] or 0,
        comprobantes=agregado["n"] or 0,
        total=total,
        igv_estimado=_igv_estimado(total),
        confirmados=confirmados,
    )


def describir_comprobantes(account_ruc: str, comprobantes) -> list[FacturaEnRiesgo]:
    """Convierte una página de comprobantes en filas listas para pintar.

    ``confirmado_en_la_fecha`` distingue los dos casos que no valen lo mismo:
    si hay una consulta anterior a la factura que ya lo daba por marcado, el
    problema es demostrable; si el proveedor cayó después, la factura puede
    estar bien y solo conviene revisarla. Mezclarlos daría un número asustadizo
    y poco accionable.
    """
    marcados = proveedores_marcados(account_ruc)
    filas = []
    for comprobante in comprobantes:
        supplier = marcados.get(comprobante.issuer_ruc)
        if supplier is None:
            continue
        total = comprobante.total_amount or CERO
        previa = (
            _estado_en_fecha(supplier, comprobante.issue_date)
            if comprobante.issue_date
            else None
        )
        filas.append(
            FacturaEnRiesgo(
                ruc_proveedor=supplier.ruc,
                proveedor=supplier.display_name,
                comprobante=comprobante.full_number
                or f"{comprobante.series}-{comprobante.number}",
                fecha=comprobante.issue_date,
                total=total,
                igv_estimado=_igv_estimado(total),
                estado_hoy=supplier.status,
                condicion_hoy=supplier.condition,
                estado_en_la_fecha=previa.status if previa else "",
                condicion_en_la_fecha=previa.condition if previa else "",
                confirmado_en_la_fecha=bool(previa and previa.has_issue),
            )
        )
    return filas
