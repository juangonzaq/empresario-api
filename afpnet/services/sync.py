"""Traer de AFPnet lo de una empresa y guardarlo.

A diferencia del resto de fuentes, esta **no puede abrir sesión sola**: el login
de AFPnet lleva un CAPTCHA y lo resuelve una persona. Aquí solo se usa la
sesión que esa persona dejó abierta, y cuando caduca se dice con claridad en
lugar de fallar con un error de red que no se parece a la causa.

Todo se guarda con ``update_or_create`` sobre la clave natural de cada tabla, de
modo que volver a sincronizar el mismo período corrige lo que cambió en vez de
duplicarlo. Importa más de lo que parece: una planilla pasa de PENDIENTE a
PAGADA días después, y es la misma planilla.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from django.utils import timezone

from accounts.models import Organization

from ..models import (
    AfpnetAffiliate, AfpnetCompany, AfpnetContribution, AfpnetDebt,
    AfpnetDeclaration, AfpnetPeriodSummary, AfpnetSession,
)
from . import client, parsers, portal

logger = logging.getLogger(__name__)

# Desde cuándo se trae el histórico la primera vez. AFPnet guarda bastante más,
# pero cada año son seis consultas —una por AFP— y lo que interesa de verdad es
# el período no prescrito.
ANIO_INICIAL = 2024


class SinSesion(Exception):
    """No hay sesión utilizable: hace falta que alguien resuelva un CAPTCHA."""


@dataclass
class Resultado:
    """Lo que el paso de sincronización cuenta en pantalla."""

    planillas: int = 0
    deudas: int = 0
    afiliados: int = 0
    periodos: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "planillas": self.planillas,
            "deudas": self.deudas,
            "afiliados": self.afiliados,
            "periodos": self.periodos,
        }


def sesion_de(organization: Organization) -> AfpnetSession:
    sesion = AfpnetSession.objects.filter(organization=organization).first()
    if sesion is None or not sesion.is_usable:
        raise SinSesion(
            "AFPnet no está conectado o su sesión caducó. Entra en tu perfil, "
            "sección AFPnet, y vuelve a iniciar sesión (hay que resolver un "
            "CAPTCHA)."
        )
    return sesion


def _guardar_empresa(organization: Organization, datos: parsers.DatosEmpresa) -> None:
    AfpnetCompany.objects.update_or_create(
        organization=organization,
        defaults={
            "taxpayer_id": organization.ruc,
            "legal_name": datos.razon_social,
            "phone": datos.telefono,
            "address": datos.direccion,
            "department": datos.departamento,
            "province": datos.provincia,
            "district": datos.distrito,
            "representative": datos.representante,
            "representative_document": datos.representante_documento,
            "representative_role": datos.representante_cargo,
            "representative_email": datos.representante_correo,
            "representative_phone": datos.representante_telefono,
            "second_signature": datos.segunda_firma,
            "fetched_at": timezone.now(),
        },
    )


def _guardar_planillas(ruc: str, planillas: list[parsers.Planilla]) -> int:
    for p in planillas:
        AfpnetDeclaration.objects.update_or_create(
            taxpayer_id=ruc, period=p.periodo, afp=p.afp,
            presentation_number=p.numero,
            defaults={
                "presented_at": p.fecha_declaracion,
                "paid_on": p.fecha_pago,
                "nominal_fondo": p.nominal_fondo,
                "nominal_ryr": p.nominal_ryr,
                "state": p.estado,
                "worker_type": p.tipo_trabajador,
                "ticket": p.ticket,
                "bank": p.banco,
                "payment_method": p.forma_pago,
                # Una planilla pasa de PENDIENTE a PAGADA días después: se
                # recalcula en cada pasada en vez de darlo por hecho.
                "is_paid": p.pagada,
            },
        )
    return len(planillas)


def _guardar_resumen(ruc: str, filas: list[parsers.ResumenDevengue]) -> int:
    for fila in filas:
        AfpnetPeriodSummary.objects.update_or_create(
            taxpayer_id=ruc, period=fila.periodo,
            defaults={
                "total_op": fila.total_op,
                "op_cierta": fila.op_cierta,
                "op_presunta": fila.op_presunta,
                "op_con_deuda": fila.op_con_deuda,
                "op_sin_deuda": fila.op_sin_deuda,
            },
        )
    return len(filas)


def _guardar_deudas(ruc: str, reporte: parsers.ReporteDeuda) -> int:
    vistas = []
    for fila in reporte.filas:
        AfpnetDebt.objects.update_or_create(
            taxpayer_id=ruc, period=fila.periodo, afp=fila.afp,
            concept=fila.tipo,
            defaults={
                "principal": fila.deuda_fondo,
                "interest": fila.deuda_seguro,
                "total": sum(
                    v for v in (fila.deuda_fondo, fila.deuda_seguro,
                                fila.deuda_comision) if v is not None
                ) or None,
                "state": fila.estado_cobranza,
            },
        )
        vistas.append((fila.periodo, fila.afp, fila.tipo))

    # Una deuda pagada desaparece del reporte. Si no se borrara, la pantalla
    # seguiría enseñando una deuda que ya no existe, que es peor que no tener
    # el dato.
    sobrantes = AfpnetDebt.objects.filter(taxpayer_id=ruc)
    for deuda in sobrantes:
        if (deuda.period, deuda.afp, deuda.concept) not in vistas:
            deuda.delete()
    return len(reporte.filas)


def _guardar_afiliado(ruc: str, ficha: parsers.Afiliado) -> None:
    AfpnetAffiliate.objects.update_or_create(
        taxpayer_id=ruc, cuspp=ficha.cuspp,
        defaults={
            "document_type": ficha.tipo_documento,
            "document_number": ficha.numero_documento,
            "full_name": ficha.nombre_completo,
            "afp": ficha.afp,
            "commission_type": ficha.tipo_comision,
            "state": ficha.situacion,
            "is_active": ficha.situacion.lower().startswith("contin"),
        },
    )


def sincronizar(
    organization: Organization,
    desde_anio: int = ANIO_INICIAL,
    hoy: date | None = None,
) -> dict[str, int]:
    """Trae de AFPnet lo de una empresa y lo guarda.

    El histórico se pide **un año por consulta**: el portal admite el rango
    entero, así que pedir mes a mes multiplicaría por doce las peticiones sin
    traer un dato más.
    """
    sesion = sesion_de(organization)
    try:
        api = portal.Portal(cookies=sesion.cookies)
        resultado = Resultado()

        _guardar_empresa(organization, api.datos_empresa())

        for inicio, fin in portal.anios_hasta_hoy(desde_anio, hoy=hoy):
            resultado.planillas += _guardar_planillas(
                organization.ruc, api.planillas(inicio, fin)
            )
            resultado.periodos += _guardar_resumen(
                organization.ruc, api.resumen_situacion(inicio, fin)
            )

        resultado.deudas = _guardar_deudas(organization.ruc, api.deudas())
        resultado.afiliados = AfpnetAffiliate.objects.filter(
            taxpayer_id=organization.ruc
        ).count()

    except client.PortalCerrado:
        # La sesión no se toca: sigue siendo válida, es el portal el que está
        # cerrado. Caducarla obligaría a resolver un CAPTCHA de más mañana.
        raise
    except client.SesionCaducada as exc:
        sesion.marcar_caducada(str(exc))
        raise SinSesion(
            "La sesión de AFPnet caducó. Vuelve a iniciarla desde tu perfil."
        ) from exc

    sesion.last_used_at = timezone.now()
    sesion.save(update_fields=["last_used_at", "updated_at"])
    logger.info(
        "AFPnet %s: %s planillas, %s períodos, %s deudas",
        organization.ruc, resultado.planillas, resultado.periodos,
        resultado.deudas,
    )
    return resultado.as_dict()


def _guardar_aportes(
    afiliado: AfpnetAffiliate, aportes: list[parsers.AporteMensual]
) -> int:
    for a in aportes:
        AfpnetContribution.objects.update_or_create(
            affiliate=afiliado, period=a.periodo,
            defaults={
                "taxpayer_id": afiliado.taxpayer_id,
                "obligation_id": a.id_obligacion,
                "kind": a.tipo,
                "has_employment": a.relacion_laboral,
                "remuneration": a.remuneracion,
                "obliged_fund": a.obligado_fondo,
                "obliged_insurance": a.obligado_seguro,
                "obliged_commission": a.obligado_comision,
                "declared_fund": a.declarado_fondo,
                "paid_fund": a.pagado_fondo,
                "declared_unpaid": a.declarado_no_pagado,
            },
        )
    return len(aportes)


def _caducar(sesion: AfpnetSession, exc: Exception) -> SinSesion:
    """Deja constancia de que la sesión murió y traduce el error.

    Sin esto la sesión seguía marcada como activa y la pantalla ofrecía
    «Sincronizar» sobre algo que ya no funcionaba; y el error salía como fallo
    genérico del portal, indistinguible de una caída de AFPnet, así que la
    interfaz no sabía que lo que tocaba era volver a entrar.
    """
    sesion.marcar_caducada(str(exc))
    return SinSesion(
        "La sesión de AFPnet caducó. Vuelve a iniciarla para continuar."
    )


def historial_de(organization: Organization, cuspp: str) -> int:
    """Refresca el historial mes a mes de un trabajador ya enrolado.

    El portal ata el historial a la sesión: hay que consultar antes su ficha o
    devolvería el del último consultado. Por eso se vuelve a pedir por
    documento en lugar de llamar directo al historial.
    """
    afiliado = AfpnetAffiliate.objects.get(
        taxpayer_id=organization.ruc, cuspp=cuspp
    )
    sesion = sesion_de(organization)
    try:
        api = portal.Portal(cookies=sesion.cookies)
        api.consultar_afiliado(afiliado.document_number)
        return _guardar_aportes(afiliado, api.historial_aportes())
    except client.SesionCaducada as exc:
        raise _caducar(sesion, exc) from exc


def enrolar_trabajador(
    organization: Organization, documento: str, tipo_documento: str = "0"
) -> AfpnetAffiliate | None:
    """Registra a un trabajador consultando su ficha en AFPnet.

    «Enrolar» aquí significa darlo de alta **en nuestra base**, no en AFPnet:
    la consulta es de solo lectura. Devuelve None si AFPnet no lo encuentra,
    que normalmente significa que no está afiliado al sistema privado.
    """
    sesion = sesion_de(organization)
    try:
        api = portal.Portal(cookies=sesion.cookies)
        ficha = api.consultar_afiliado(documento, tipo_documento)
    except client.SesionCaducada as exc:
        raise _caducar(sesion, exc) from exc
    if ficha is None or not ficha.cuspp:
        return None
    _guardar_afiliado(organization.ruc, ficha)
    afiliado = AfpnetAffiliate.objects.get(
        taxpayer_id=organization.ruc, cuspp=ficha.cuspp
    )
    # El historial es un extra: si no se puede traer, el trabajador queda
    # enrolado igualmente. `getSessionOpListJson` cuelga del módulo de
    # obligación de pago y exige que se haya seleccionado al afiliado **dentro
    # de esa pantalla**; llegar por la consulta de afiliado no basta y el
    # portal responde 500. Mientras no se resuelva esa navegación, perder el
    # historial no puede costar el alta.
    try:
        _guardar_aportes(afiliado, api.historial_aportes())
    except client.AfpnetError:
        logger.warning(
            "Sin historial de aportes para %s: el portal exige seleccionarlo "
            "desde la pantalla de obligación de pago", afiliado.cuspp,
        )
    return afiliado
