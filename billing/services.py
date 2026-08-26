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

# Sin I, O, 0, 1: se dicta por teléfono y se escribe en el celular.
_ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_referral_code() -> str:
    while True:
        code = "".join(secrets.choice(_ALFABETO) for _ in range(8))
        if not User.objects.filter(referral_code=code).exists():
            return code


def trial_days() -> int:
    return int(getattr(settings, "BILLING_TRIAL_DAYS", 7))


def referral_target() -> int:
    return int(getattr(settings, "REFERRAL_TARGET", 5))


def reward_days() -> int:
    return int(getattr(settings, "REFERRAL_REWARD_DAYS", 30))


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
    return company_seat_base(user) + int(user.extra_company_seats or 0)


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
    extra = int(user.extra_company_seats or 0)
    used = companies_in_use(user)
    limit = base + extra
    return {
        "included": base,
        "extra": extra,
        "limit": limit,
        "used": used,
        "available": max(0, limit - used),
        "extra_price": str(extra_company_price()),
        "currency": "PEN",
    }


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


def summary(organization: Organization) -> dict:
    sub = ensure_subscription(organization)
    return {
        "status": sub.status,
        "plan": sub.plan.code if sub.plan else None,
        "trial_end": sub.trial_end,
        "current_period_end": sub.current_period_end,
        "access_until": sub.access_until,
        "days_left": sub.days_left,
        "is_active": sub.is_active,
        "auto_renew": sub.auto_renew,
        "next_charge_at": sub.next_charge_at,
    }


# ------------------------------------------------------------------ pagos

def start_checkout(organization: Organization, plan: Plan, user: User, request=None) -> Payment:
    """Inicia el pago del plan. Si el plan es recurrente y la pasarela sabe
    de suscripciones, se crea una suscripción en la pasarela (renovación
    automática); si no, un pago suelto por el periodo."""
    from .gateways import get_gateway

    gateway = get_gateway()
    # Antes de dejar rastro: sin pasarela no hay pago pendiente ni correo.
    gateway.ensure_available()
    sub = ensure_subscription(organization)
    recurring = plan.recurring and gateway.supports_recurring
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
    sub.canceled_at = timezone.now()
    sub.save(update_fields=["auto_renew", "gateway_status", "next_charge_at", "canceled_at", "updated_at"])
    if user is not None:
        emails.renovacion_cancelada(sub, user)
    return sub


def record_recurring_charge(sub: Subscription, *, amount, currency: str, gateway_payment_id: str, raw: dict | None = None) -> Payment:
    """Un cobro periódico avisado por la pasarela. El primero aprueba el alta
    pendiente; los siguientes crean su propio pago. Idempotente por id."""
    existing = Payment.objects.filter(subscription=sub, gateway_payment_id=gateway_payment_id).first()
    if existing:
        return existing
    setup = Payment.objects.filter(
        subscription=sub, kind=PaymentKind.RECURRING_SETUP, status=PaymentStatus.PENDING,
    ).order_by("-created_at").first()
    if setup is not None:
        return approve_payment(setup, gateway_payment_id=gateway_payment_id, raw=raw)
    last = Payment.objects.filter(subscription=sub).order_by("-created_at").first()
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
    reward.applied_to = sub
    reward.applied_at = timezone.now()
    reward.save(update_fields=["applied_to", "applied_at", "updated_at"])
    logger.info("Mes gratis aplicado a %s por referidos de %s", sub.organization.ruc, reward.user.email)
    from . import emails

    emails.mes_gratis(reward)
    return True


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
