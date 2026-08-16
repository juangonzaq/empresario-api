"""Enlazar el registro de colaboradores con lo que ya sabemos por AFPnet.

Dos operaciones, y las dos van en el mismo sentido: AFPnet manda sobre quién es
la persona (nombre, documento, AFP, si sigue en planilla) y **propone** su
sueldo; la empresa manda sobre el sueldo en cuanto lo escribe a mano.

Esa regla es la que evita el efecto más molesto posible: que un sueldo
corregido a mano vuelva al valor viejo cada vez que alguien abre la pantalla.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from afpnet.models import AfpnetAffiliate, AfpnetContribution

from .models import Colaborador, OrigenSueldo, RegimenPensionario

logger = logging.getLogger(__name__)


def _ultimas_remuneraciones(ruc: str) -> dict[UUID, tuple[Decimal, str]]:
    """La última remuneración declarada de cada afiliado de la empresa.

    Se resuelve en una sola consulta para todos, en vez de una por persona:
    esto se ejecuta cada vez que se abre la lista.

    Se toma ``remuneration`` —el sueldo sobre el que se calculó el aporte— y no
    lo pagado al fondo, que es el 10% largo de esa cifra. Los meses sin
    remuneración se descartan: un mes de licencia sin goce declara cero y no
    significa que a esa persona se le haya bajado el sueldo.
    """
    ultimas: dict[UUID, tuple[Decimal, str]] = {}
    filas = (
        AfpnetContribution.objects.filter(
            taxpayer_id=ruc, remuneration__isnull=False
        )
        .exclude(remuneration=0)
        # Ascendente a propósito: cada fila pisa a la anterior, así que al
        # terminar el diccionario tiene el período más alto de cada afiliado.
        .order_by("affiliate_id", "period")
        .values_list("affiliate_id", "period", "remuneration")
    )
    for affiliate_id, period, remuneration in filas:
        ultimas[affiliate_id] = (remuneration, period)
    return ultimas


def ultima_remuneracion(colaborador: Colaborador) -> tuple[Decimal, str] | None:
    """Lo mismo para una sola persona. None si AFPnet no ha declarado nada."""
    if not colaborador.cuspp:
        return None
    fila = (
        AfpnetContribution.objects.filter(
            taxpayer_id=colaborador.taxpayer_id,
            affiliate__cuspp=colaborador.cuspp,
            remuneration__isnull=False,
        )
        .exclude(remuneration=0)
        .order_by("-period")
        .values_list("period", "remuneration")
        .first()
    )
    if fila is None:
        return None
    period, remuneration = fila
    return remuneration, period


def aplicar_sueldo_afpnet(colaborador: Colaborador) -> bool:
    """Pone en el colaborador su última remuneración declarada.

    Devuelve si había algo que poner. No guarda: quien llama decide cuándo.
    """
    ultima = ultima_remuneracion(colaborador)
    if ultima is None:
        return False
    colaborador.monthly_salary, colaborador.salary_period = ultima
    colaborador.salary_source = OrigenSueldo.AFPNET
    colaborador.salary_updated_at = timezone.now()
    return True


# Campos que copia AFPnet tal cual. El sueldo va aparte porque su regla no es
# «copiar», sino «copiar salvo que alguien lo haya escrito».
def _identidad(afiliado: AfpnetAffiliate, colaborador: Colaborador) -> dict:
    return {
        "cuspp": afiliado.cuspp,
        # Los `or` de vuelta no son un adorno: AFPnet devuelve el tipo de
        # documento en blanco en algunas fichas, y sobrescribir con vacío lo
        # que ya se sabía sería perder dato a cambio de nada.
        "document_type": afiliado.document_type or colaborador.document_type,
        "document_number": afiliado.document_number or colaborador.document_number,
        "full_name": afiliado.full_name or colaborador.full_name,
        "regimen": RegimenPensionario.AFP,
        "afp": afiliado.afp,
        "is_active": afiliado.is_active,
    }


def sincronizar_desde_afpnet(ruc: str) -> int:
    """Refleja en el registro a los afiliados que ya trajo AFPnet.

    Se llama al listar. Podría llamarse solo al enrolar, pero entonces un
    historial traído después —que es cuando aparece la remuneración— no
    actualizaría ningún sueldo hasta el siguiente alta, y el usuario vería un
    importe viejo sin entender por qué.

    Devuelve cuántas fichas cambiaron, que es lo único que interesa contar: en
    una pantalla que se abre a menudo, lo normal es que el resultado sea cero y
    no se escriba nada.
    """
    afiliados = list(AfpnetAffiliate.objects.filter(taxpayer_id=ruc))
    if not afiliados:
        return 0

    ultimas = _ultimas_remuneraciones(ruc)
    existentes = list(Colaborador.objects.filter(taxpayer_id=ruc))
    por_cuspp = {c.cuspp: c for c in existentes if c.cuspp}
    por_documento = {c.document_number: c for c in existentes if c.document_number}

    tocados = 0
    for afiliado in afiliados:
        # El CUSPP es la llave buena; el documento solo sirve para reconocer al
        # que la empresa dio de alta a mano antes de que AFPnet lo conociera —y
        # es exactamente el caso que hace falta: se registra al recién
        # contratado, elige AFP semanas después, y no queremos dos fichas.
        colaborador = (
            por_cuspp.get(afiliado.cuspp)
            or por_documento.get(afiliado.document_number)
            or Colaborador(taxpayer_id=ruc)
        )
        cambios = _identidad(afiliado, colaborador)

        ultima = ultimas.get(afiliado.id)
        es_nuevo = colaborador.pk is None
        # El sueldo escrito a mano es la palabra de la empresa sobre lo que
        # paga: solo se toca lo que vino de AFPnet o lo que aún no existe.
        if ultima is not None and (
            es_nuevo
            or colaborador.monthly_salary is None
            or colaborador.salary_source == OrigenSueldo.AFPNET
        ):
            cambios["monthly_salary"], cambios["salary_period"] = ultima
            cambios["salary_source"] = OrigenSueldo.AFPNET

        distintos = [
            campo for campo, valor in cambios.items()
            if getattr(colaborador, campo) != valor
        ]
        if not distintos:
            continue

        for campo in distintos:
            setattr(colaborador, campo, cambios[campo])
        if "monthly_salary" in distintos:
            colaborador.salary_updated_at = timezone.now()

        try:
            with transaction.atomic():
                colaborador.save()
        except IntegrityError:
            # Dos afiliados con el mismo documento y distinto CUSPP: pasa
            # cuando una AFP registró mal a alguien. Se deja la ficha que ya
            # había y se anota; perder el alta entera por esto sería peor.
            logger.warning(
                "Colaborador duplicado al sincronizar %s: documento %s, CUSPP %s",
                ruc, afiliado.document_number, afiliado.cuspp,
            )
            continue

        por_cuspp[colaborador.cuspp] = colaborador
        if colaborador.document_number:
            por_documento[colaborador.document_number] = colaborador
        tocados += 1

    return tocados


def proximo_cumple(nacimiento: date, desde: date) -> date:
    """La próxima ocurrencia del cumpleaños, `desde` incluido.

    Un 29 de febrero cae en 1 de marzo los años no bisiestos, que es como
    suele celebrarse.
    """

    def en(anio: int) -> date:
        try:
            return nacimiento.replace(year=anio)
        except ValueError:  # 29 de febrero en año no bisiesto
            return date(anio, 3, 1)

    ocurrencia = en(desde.year)
    return ocurrencia if ocurrencia >= desde else en(desde.year + 1)


def eventos_cumpleanos(taxpayer_id: str, desde: date) -> list[dict]:
    """Los cumpleaños de la planilla como eventos de calendario.

    Mismo contrato que ``sensor_sunat.calendario.eventos_para``: cada evento
    lleva fecha, tipo, título, descripción, alarmas y recurrencia. Solo gente
    en planilla y con fecha registrada. Estos eventos van únicamente al
    calendario autenticado: son datos personales y no pueden salir por la
    suscripción de token, cuya URL es la única credencial.
    """
    eventos = []
    activos = Colaborador.objects.filter(
        taxpayer_id=taxpayer_id, is_active=True
    ).exclude(birth_date=None)
    for colaborador in activos:
        fecha = proximo_cumple(colaborador.birth_date, desde)
        edad = fecha.year - colaborador.birth_date.year
        cargo = f" ({colaborador.position})" if colaborador.position else ""
        eventos.append(dict(
            fecha=fecha,
            tipo="CUMPLEANOS",
            titulo=f"🎂 Cumpleaños de {colaborador.full_name}",
            descripcion=f"Cumple {edad} años.{cargo}".strip(),
            alarmas_dias=[1],
            recurrencia=None,
        ))
    eventos.sort(key=lambda e: (e["fecha"], e["titulo"]))
    return eventos
