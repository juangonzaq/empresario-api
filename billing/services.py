"""Reglas de negocio de suscripciones y referidos. Las vistas solo llaman aquí."""

from __future__ import annotations

import datetime
import logging
import secrets
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.models import Membership, Organization, Role, User

from .models import (
    Payment, PaymentKind, PaymentStatus, Plan, Referral, ReferralReward, Subscription,
    UsageCharge, UsageChargeKind,
)

logger = logging.getLogger(__name__)

# Solo números: 6 dígitos se dictan por teléfono y se escriben en el celular
# sin confundir letras. Un millón de combinaciones sobra para la base actual;
# el bucle resuelve la colisión ocasional.
_DIGITOS = "0123456789"


def new_referral_code() -> str:
    while True:
        code = "".join(secrets.choice(_DIGITOS) for _ in range(6))
        if not User.objects.filter(referral_code=code).exists():
            return code


def trial_days() -> int:
    return int(getattr(settings, "BILLING_TRIAL_DAYS", 7))


def referral_target() -> int:
    return int(getattr(settings, "REFERRAL_TARGET", 5))


def reward_days() -> int:
    return int(getattr(settings, "REFERRAL_REWARD_DAYS", 30))


def has_paid_plan(organization) -> bool:
    """True solo con un periodo PAGADO vigente; la prueba gratuita no cuenta.

    Gatea las funciones de IA (chat Vigía, lectura IA del buzón): cada uso
    cuesta tokens de verdad, así que viven en la versión de pago.
    """
    return ensure_subscription(organization).status == "active"


# ------------------------------------------------------- asientos de empresa

def default_company_seats() -> int:
    return int(getattr(settings, "BILLING_DEFAULT_COMPANY_SEATS", 3))


def extra_company_price() -> Decimal:
    return Decimal(str(getattr(settings, "BILLING_EXTRA_COMPANY_PRICE", "9")))


def company_seat_base(user: User) -> int:
    """Asientos que incluye el plan del titular; si aún no tiene plan (prueba),
    el base por defecto del sistema."""
    sub = _primary_subscription(user)
    if sub and sub.plan and sub.plan.included_company_seats:
        return sub.plan.included_company_seats
    return default_company_seats()


def companies_in_use(user: User) -> int:
    return Membership.objects.filter(
        user=user, is_active=True, role=Role.OWNER
    ).count()


def company_seat_limit(user: User) -> int:
    """Base del plan + cortesías del admin + asientos comprados en la
    suscripción de la empresa principal."""
    sub = _primary_subscription(user)
    comprados = int(sub.extra_company_seats) if sub else 0
    return company_seat_base(user) + int(user.extra_company_seats or 0) + comprados


# ------------------------------------------------------ asientos: personas

def default_member_seats() -> int:
    return int(getattr(settings, "BILLING_DEFAULT_MEMBER_SEATS", 3))


def member_seat_limit(organization: Organization) -> int:
    """Personas que caben en una empresa: las del plan más las compradas."""
    sub = ensure_subscription(organization)
    plan = plan_vigente(sub)
    base = plan.included_member_seats if plan else default_member_seats()
    return base + int(sub.extra_member_seats)


def members_in_use(organization: Organization) -> int:
    """Accesos activos más invitaciones pendientes: una invitación ya ocupa
    el asiento, si no se podría invitar sin tope y pagar nunca."""
    from accounts.models import Invitation, InvitationStatus

    return (
        Membership.objects.filter(organization=organization, is_active=True).count()
        + Invitation.objects.filter(organization=organization, status=InvitationStatus.PENDING).count()
    )


def can_add_member(organization: Organization) -> bool:
    return members_in_use(organization) < member_seat_limit(organization)


def member_seat_summary(organization: Organization) -> dict:
    sub = ensure_subscription(organization)
    plan = plan_vigente(sub)
    included = plan.included_member_seats if plan else default_member_seats()
    extra = int(sub.extra_member_seats)
    used = members_in_use(organization)
    limit = included + extra
    return {
        "included": included, "extra": extra, "limit": limit, "used": used,
        "available": max(0, limit - used),
        "extra_price": str(plan.extra_member_seat_price if plan else extra_company_price()),
        "currency": plan.currency if plan else "PEN",
    }


# ------------------------------------------------------ add-ons contratados

class AddonsUnavailable(Exception):
    """No hay una suscripción con renovación sobre la que cargar asientos."""


def subscription_amount(sub: Subscription) -> Decimal:
    """Lo que cobra cada ciclo la pasarela: plan + asientos × meses del plan."""
    plan = plan_vigente(sub)
    if plan is None:
        return Decimal("0")
    extras = (
        Decimal(sub.extra_member_seats) * plan.extra_member_seat_price
        + Decimal(sub.extra_company_seats) * plan.extra_company_seat_price
    )
    return plan.price + extras * plan.months


def addons_summary(sub: Subscription) -> dict:
    plan = plan_vigente(sub)
    return {
        "available": bool(plan) and sub.auto_renew,
        "member_seats": int(sub.extra_member_seats),
        "company_seats": int(sub.extra_company_seats),
        "member_price": str(plan.extra_member_seat_price) if plan else None,
        "company_price": str(plan.extra_company_seat_price) if plan else None,
        "included_member_seats": plan.included_member_seats if plan else default_member_seats(),
        "included_company_seats": plan.included_company_seats if plan else default_company_seats(),
        "monthly_extra": str(
            Decimal(sub.extra_member_seats) * plan.extra_member_seat_price
            + Decimal(sub.extra_company_seats) * plan.extra_company_seat_price
        ) if plan else "0",
        "cycle_amount": str(subscription_amount(sub)) if plan else None,
        "currency": plan.currency if plan else "PEN",
    }


def set_addons(organization: Organization, *, member_seats: int, company_seats: int, user: User) -> dict:
    """Fija cuántos asientos adicionales tiene la suscripción de la empresa.

    Se activan al instante y se cobran desde el próximo ciclo: la pasarela
    pasa a cobrar `subscription_amount`. Bajar solo se permite si lo que queda
    alcanza para lo que ya está en uso."""
    from .gateways import get_gateway

    sub = ensure_subscription(organization)
    plan = plan_vigente(sub)
    if plan is None or not sub.auto_renew:
        raise AddonsUnavailable(
            "Para añadir asientos necesitas un plan con renovación automática activa."
        )
    if member_seats < 0 or company_seats < 0:
        raise ValueError("La cantidad no puede ser negativa.")
    if members_in_use(organization) > plan.included_member_seats + member_seats:
        raise ValueError(
            f"Esta empresa usa {members_in_use(organization)} accesos; quita personas "
            f"antes de reducir asientos."
        )
    principal = _primary_subscription(user)
    if principal and principal.pk == sub.pk:
        base = company_seat_base(user) + int(user.extra_company_seats or 0)
        if companies_in_use(user) > base + company_seats:
            raise ValueError(
                f"Administras {companies_in_use(user)} empresas; no puedes bajar de ahí."
            )
    sub.extra_member_seats = member_seats
    sub.extra_company_seats = company_seats
    if sub.gateway_subscription_id:
        get_gateway().update_amount(sub, subscription_amount(sub))
    sub.save(update_fields=["extra_member_seats", "extra_company_seats", "updated_at"])
    logger.info(
        "Asientos de %s: %s personas, %s empresas · ciclo %s",
        organization.ruc, member_seats, company_seats, subscription_amount(sub),
    )
    return addons_summary(sub)


def can_add_company(user: User) -> bool:
    return companies_in_use(user) < company_seat_limit(user)


# ------------------------------------------------------- cargos por uso

def extra_manual_sync_price() -> Decimal:
    return Decimal(str(getattr(settings, "SYNC_EXTRA_MANUAL_PRICE", "5")))


def record_usage_charge(organization, kind: str, amount: Decimal, *,
                        description: str = "", reference: str = "", user: User | None = None) -> UsageCharge:
    """Registra un cargo por uso (no lo cobra al instante). Se liquida luego
    desde la facturación de la empresa."""
    charge = UsageCharge.objects.create(
        organization=organization, kind=kind, amount=amount,
        description=description, reference=reference, created_by=user,
    )
    logger.info("Cargo por uso %s S/%s para %s", kind, amount, organization.ruc)
    return charge


def charge_extra_manual_sync(organization, *, reference: str = "", user: User | None = None) -> UsageCharge:
    """El cargo de una sincronización manual por encima del tope diario."""
    price = extra_manual_sync_price()
    return record_usage_charge(
        organization, UsageChargeKind.EXTRA_MANUAL_SYNC, price,
        description="Sincronización manual adicional",
        reference=reference, user=user,
    )


def usage_charges(organization, limit: int = 50):
    return list(UsageCharge.objects.filter(organization=organization)[:limit])


def seat_summary(user: User) -> dict:
    """Cuántas empresas puede tener el titular y cuántas usa; para el front."""
    base = company_seat_base(user)
    sub = _primary_subscription(user)
    extra = int(user.extra_company_seats or 0) + (int(sub.extra_company_seats) if sub else 0)
    used = companies_in_use(user)
    limit = base + extra
    return {
        "included": base,
        "extra": extra,
        "limit": limit,
        "used": used,
        "available": max(0, limit - used),
        "extra_price": str(_extra_company_price_for(user)),
        "currency": "PEN",
    }


def _extra_company_price_for(user: User) -> Decimal:
    sub = _primary_subscription(user)
    plan = plan_vigente(sub) if sub else None
    return plan.extra_company_seat_price if plan else extra_company_price()


# ------------------------------------------------------------- suscripción

def ensure_subscription(organization: Organization) -> Subscription:
    """La suscripción de la empresa; si no existe, nace en prueba gratuita."""
    sub = getattr(organization, "subscription", None)
    if sub is not None:
        return sub
    sub, _ = Subscription.objects.get_or_create(
        organization=organization,
        defaults={"trial_end": timezone.now() + datetime.timedelta(days=trial_days())},
    )
    return sub


def plan_vigente(sub: Subscription) -> Plan | None:
    """El plan al que la empresa está suscrita.

    `sub.plan` se fija con el primer pago aprobado; pero con la renovación ya
    autorizada en la pasarela (durante la prueba, antes del primer cobro) la
    persona YA está suscrita al plan del alta. Sin esto, la pantalla le
    volvía a ofrecer el mismo plan y el API le dejaba contratarlo dos veces."""
    if sub.plan_id:
        return sub.plan
    if not sub.auto_renew:
        return None
    setup = (
        Payment.objects.filter(subscription=sub, kind=PaymentKind.RECURRING_SETUP)
        .exclude(status=PaymentStatus.CANCELED).select_related("plan")
        .order_by("-created_at").first()
    )
    return setup.plan if setup else None


def summary(organization: Organization) -> dict:
    sub = ensure_subscription(organization)
    plan = plan_vigente(sub)
    return {
        "status": sub.status,
        "plan": plan.code if plan else None,
        "trial_end": sub.trial_end,
        "current_period_end": sub.current_period_end,
        "access_until": sub.access_until,
        "days_left": sub.days_left,
        "is_active": sub.is_active,
        "auto_renew": sub.auto_renew,
        "next_charge_at": sub.next_charge_at,
        "paused_until": sub.paused_until,
        "addons": addons_summary(sub),
    }


# ------------------------------------------------------------------ pagos

class AlreadySubscribed(Exception):
    """Ya hay una suscripción vigente con ese mismo plan: no hay nada que pagar."""


def start_checkout(organization: Organization, plan: Plan, user: User, request=None) -> Payment:
    """Inicia el pago del plan. Si el plan es recurrente y la pasarela sabe
    de suscripciones, se crea una suscripción en la pasarela (renovación
    automática); si no, un pago suelto por el periodo."""
    from .gateways import get_gateway

    gateway = get_gateway()
    # Antes de dejar rastro: sin pasarela no hay pago pendiente ni correo.
    gateway.ensure_available()
    sub = ensure_subscription(organization)
    # Una suscripción es una sola. Con renovación automática y el mismo plan
    # no hay nada que contratar: sería cobrar dos veces lo mismo. Cambiar al
    # otro plan sí se permite (la pasarela cancela la anterior al autorizarse).
    vigente = plan_vigente(sub)
    if sub.auto_renew and vigente is not None and vigente.id == plan.id:
        raise AlreadySubscribed(
            f"Ya tienes el plan {plan.name} con renovación automática. "
            "Si quieres otro plan, elige el otro; si quieres dejar de pagar, cancela la suscripción."
        )
    recurring = plan.recurring and gateway.supports_recurring
    # Un checkout nuevo reemplaza a los intentos anteriores que nunca se
    # completaron (la persona cerró la pestaña, la pasarela rechazó…). Si no,
    # cada clic en «Continuar» deja un «pendiente» eterno en el historial.
    Payment.objects.filter(
        subscription=sub, status=PaymentStatus.PENDING, paid_at__isnull=True,
    ).update(status=PaymentStatus.CANCELED, updated_at=timezone.now())
    payment = Payment.objects.create(
        subscription=sub, plan=plan, amount=plan.price, currency=plan.currency,
        gateway=gateway.name, created_by=user,
        kind=PaymentKind.RECURRING_SETUP if recurring else PaymentKind.ONE_OFF,
    )
    if recurring:
        gateway.create_subscription(payment, request)
    else:
        gateway.create_checkout(payment, request)
    return payment


def cancel_auto_renew(organization: Organization, user: User | None = None) -> Subscription:
    """Apaga la renovación automática. Lo pagado sigue vigente hasta su fin."""
    from . import emails
    from .gateways import get_gateway

    sub = ensure_subscription(organization)
    if sub.gateway_subscription_id:
        get_gateway().cancel_subscription(sub)
    sub.auto_renew = False
    sub.gateway_status = "cancelled" if sub.gateway_subscription_id else sub.gateway_status
    sub.next_charge_at = None
    sub.paused_until = None
    sub.canceled_at = timezone.now()
    sub.save(update_fields=["auto_renew", "gateway_status", "next_charge_at", "paused_until", "canceled_at", "updated_at"])
    if user is not None:
        emails.renovacion_cancelada(sub, user)
    return sub


def record_recurring_charge(sub: Subscription, *, amount, currency: str, gateway_payment_id: str, raw: dict | None = None) -> Payment | None:
    """Un cobro periódico avisado por la pasarela. El primero aprueba el alta
    pendiente; los siguientes crean su propio pago. Idempotente por id.

    Devuelve None si el importe no es el de un periodo: Mercado Pago valida
    la tarjeta con un cargo mínimo (S/ 2 en Perú) que devuelve enseguida
    cuando el primer cobro queda diferido por la prueba gratis. Llega por el
    mismo webhook y con la misma referencia que un cobro real; sin este
    filtro aprobaba el alta, mandaba «Pago recibido» por el importe del plan
    y regalaba un mes. Ningún cobro de verdad baja de la mitad del precio
    del plan: los asientos adicionales solo suman."""
    existing = Payment.objects.filter(subscription=sub, gateway_payment_id=gateway_payment_id).first()
    if existing:
        return existing
    setup = Payment.objects.filter(
        subscription=sub, kind=PaymentKind.RECURRING_SETUP, status=PaymentStatus.PENDING,
    ).order_by("-created_at").first()
    last = Payment.objects.filter(subscription=sub).order_by("-created_at").first()
    plan = setup.plan if setup else (sub.plan or (last.plan if last else None))
    if plan is not None and Decimal(str(amount)) < plan.price / 2:
        logger.info(
            "Cobro de %s %s en %s ignorado: validación de tarjeta, no un periodo del plan %s",
            currency, amount, sub.organization.ruc, plan.code,
        )
        return None
    if setup is not None:
        return approve_payment(setup, gateway_payment_id=gateway_payment_id, raw=raw)
    payment = Payment.objects.create(
        subscription=sub, plan=sub.plan or (last.plan if last else None), amount=amount, currency=currency,
        gateway=sub.gateway or (last.gateway if last else ""), kind=PaymentKind.RECURRING_CHARGE,
        created_by=last.created_by if last else None,
    )
    return approve_payment(payment, gateway_payment_id=gateway_payment_id, raw=raw)


@transaction.atomic
def approve_payment(payment: Payment, *, gateway_payment_id: str = "", raw: dict | None = None) -> Payment:
    """Marca el pago como aprobado y alarga la suscripción. Idempotente: la
    pasarela puede avisar dos veces del mismo pago."""
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.status == PaymentStatus.APPROVED:
        return payment
    sub = Subscription.objects.select_for_update().get(pk=payment.subscription_id)
    now = timezone.now()
    # Pagar durante la prueba no la quema: el periodo empieza cuando acabe lo
    # que ya tenía vigente.
    start = max(sub.access_until, now)
    end = start + datetime.timedelta(days=30 * payment.plan.months)
    sub.plan = payment.plan
    sub.current_period_end = end
    sub.canceled_at = None
    sub.save(update_fields=["plan", "current_period_end", "canceled_at", "updated_at"])

    payment.status = PaymentStatus.APPROVED
    payment.paid_at = now
    payment.period_start = start
    payment.period_end = end
    if gateway_payment_id:
        payment.gateway_payment_id = gateway_payment_id
    if raw is not None:
        payment.raw = raw
    payment.save()
    logger.info("Pago aprobado %s · %s hasta %s", payment.pk, sub.organization.ruc, end.date())

    from . import emails

    emails.pago_aprobado(payment)
    if payment.created_by_id:
        register_conversion(payment)
    return payment


def reject_payment(payment: Payment, *, status: str = PaymentStatus.REJECTED, raw: dict | None = None) -> Payment:
    if payment.status == PaymentStatus.APPROVED:
        return payment
    payment.status = status
    if raw is not None:
        payment.raw = raw
    payment.save(update_fields=["status", "raw", "updated_at"])
    return payment


# -------------------------------------------------------------- referidos

def link_referral(referred: User, code: str) -> Referral | None:
    """Anota quién trajo a ``referred``. Ignora códigos desconocidos o propios."""
    code = (code or "").strip().upper()
    if not code:
        return None
    referrer = User.objects.filter(referral_code=code).exclude(pk=referred.pk).first()
    if referrer is None or hasattr(referred, "referral"):
        return None
    referred.referred_by = referrer
    referred.save(update_fields=["referred_by"])
    return Referral.objects.create(referrer=referrer, referred=referred)


def register_conversion(payment: Payment) -> None:
    """Si quien pagó fue referido y aún no contaba, ahora cuenta; y si con eso
    el referente llega a un múltiplo de la meta, gana un mes."""
    referral = Referral.objects.filter(referred_id=payment.created_by_id, converted_at__isnull=True).first()
    if referral is None:
        return
    referral.converted_at = timezone.now()
    referral.converted_by = payment
    referral.save(update_fields=["converted_at", "converted_by", "updated_at"])
    grant_pending_rewards(referral.referrer)


def grant_pending_rewards(user: User) -> list[ReferralReward]:
    conversions = Referral.objects.filter(referrer=user, converted_at__isnull=False).count()
    earned = conversions // referral_target()
    granted = ReferralReward.objects.filter(user=user).count()
    new = []
    while granted < earned:
        granted += 1
        reward = ReferralReward.objects.create(
            user=user, days=reward_days(), conversions_at_grant=granted * referral_target(),
        )
        if not apply_reward(reward):
            # Sin empresa todavía: igual se le cuenta la buena noticia.
            from . import emails

            emails.mes_gratis(reward)
        new.append(reward)
    return new


def _primary_subscription(user: User) -> Subscription | None:
    """La empresa «propia» del referente: donde es titular, la más antigua."""
    membership = (
        Membership.objects.filter(user=user, is_active=True, role=Role.OWNER)
        .select_related("organization").order_by("created_at").first()
    ) or (
        Membership.objects.filter(user=user, is_active=True, role=Role.ACCOUNTANT)
        .select_related("organization").order_by("created_at").first()
    )
    return ensure_subscription(membership.organization) if membership else None


def apply_reward(reward: ReferralReward) -> bool:
    if reward.applied_at:
        return True
    sub = _primary_subscription(reward.user)
    if sub is None:
        return False
    sub.extend(reward.days)
    sub.save(update_fields=["trial_end", "current_period_end", "bonus_days", "updated_at"])
    _posponer_cobro(sub, reward.days)
    reward.applied_to = sub
    reward.applied_at = timezone.now()
    reward.save(update_fields=["applied_to", "applied_at", "updated_at"])
    logger.info("Mes gratis aplicado a %s por referidos de %s", sub.organization.ruc, reward.user.email)
    from . import emails

    emails.mes_gratis(reward)
    return True


def _posponer_cobro(sub: Subscription, days: int) -> None:
    """Con renovación automática, el mes gratis tiene que ser un mes SIN cobro.

    Alargar el acceso local no basta: la pasarela cobra en su fecha igual. Se
    pausa la suscripción en la pasarela y se reanuda (`paused_until`, tarea
    `billing.reanudar_suscripciones_pausadas`) `days` después de la fecha en
    que tocaba cobrar. Si la pasarela falla, el acceso local ya quedó
    alargado y se deja constancia en el log."""
    if not (sub.auto_renew and sub.gateway_subscription_id):
        return
    from .gateways import get_gateway

    base = sub.paused_until or sub.next_charge_at or timezone.now()
    hasta = base + datetime.timedelta(days=days)
    try:
        if not sub.paused_until:
            get_gateway().pause_subscription(sub)
    except Exception:  # noqa: BLE001 — se alarga el acceso igual; el cobro no se pospone
        logger.exception("No se pudo pausar en la pasarela la suscripción de %s", sub.organization.ruc)
        return
    sub.paused_until = hasta
    sub.next_charge_at = hasta
    sub.gateway_status = "paused"
    sub.save(update_fields=["paused_until", "next_charge_at", "gateway_status", "updated_at"])
    logger.info("Cobro de %s pospuesto hasta %s por referidos", sub.organization.ruc, hasta.date())


def reanudar_suscripciones_pausadas(now=None) -> int:
    """Reanuda en la pasarela las suscripciones cuyo mes gratis ya pasó."""
    from .gateways import get_gateway

    now = now or timezone.now()
    reanudadas = 0
    for sub in Subscription.objects.filter(paused_until__lte=now, auto_renew=True).select_related("organization"):
        try:
            get_gateway().resume_subscription(sub)
        except Exception:  # noqa: BLE001 — se reintenta en la próxima pasada
            logger.exception("No se pudo reanudar la suscripción de %s", sub.organization.ruc)
            continue
        sub.paused_until = None
        sub.gateway_status = "authorized"
        sub.save(update_fields=["paused_until", "gateway_status", "updated_at"])
        reanudadas += 1
    return reanudadas


def apply_pending_rewards(user: User) -> None:
    for reward in ReferralReward.objects.filter(user=user, applied_at__isnull=True):
        apply_reward(reward)


def referral_summary(user: User) -> dict:
    total = Referral.objects.filter(referrer=user).count()
    converted = Referral.objects.filter(referrer=user, converted_at__isnull=False).count()
    target = referral_target()
    return {
        "code": user.referral_code,
        "link": f"{settings.FRONTEND_URL}/registro?ref={user.referral_code}",
        "target": target,
        "reward_days": reward_days(),
        "referred": total,
        "converted": converted,
        "progress": converted % target,
        "rewards": [
            {
                "days": r.days,
                "granted_at": r.created_at,
                "applied_at": r.applied_at,
                "applied_to": r.applied_to.organization.ruc if r.applied_to else None,
            }
            for r in ReferralReward.objects.filter(user=user).select_related("applied_to__organization")
        ],
    }
