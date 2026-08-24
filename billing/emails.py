"""Correos de suscripción: recibo, renovación cancelada, mes gratis, fin de prueba."""

from __future__ import annotations

from django.conf import settings
from django.utils import timezone
from django.utils.formats import date_format

from core.emails import send_email

from .models import Payment, ReferralReward, Subscription


def _fecha(dt) -> str:
    return date_format(timezone.localtime(dt), "j \\d\\e F \\d\\e Y") if dt else "—"


def _importe(payment: Payment) -> str:
    return f"{payment.currency} {payment.amount:,.2f}".replace("PEN ", "S/ ")


def pago_aprobado(payment: Payment) -> None:
    user = payment.created_by
    if user is None:
        return
    sub = payment.subscription
    org = sub.organization
    url = f"{settings.FRONTEND_URL}/suscripcion"
    renov = None
    if sub.auto_renew:
        renov = "automática, cada " + ("año" if payment.plan.months == 12 else "mes")
    send_email(
        "Pago recibido", user.email, "pago_aprobado",
        {"empresa": f"{org.display_name} · {org.ruc}", "plan": payment.plan.name,
         "importe": _importe(payment), "hasta": _fecha(sub.current_period_end),
         "renovacion": renov, "referencia": str(payment.pk)[:8].upper(), "url": url},
        text=(
            f"Recibimos tu pago de {_importe(payment)} por el plan {payment.plan.name} "
            f"de {org.display_name} ({org.ruc}). Vigente hasta el {_fecha(sub.current_period_end)}.\n\n"
            f"Tu suscripción: {url}\n"
        ),
    )


def renovacion_cancelada(sub: Subscription, user) -> None:
    org = sub.organization
    url = f"{settings.FRONTEND_URL}/suscripcion"
    send_email(
        "Renovación automática cancelada", user.email, "renovacion_cancelada",
        {"empresa": f"{org.display_name} · {org.ruc}", "hasta": _fecha(sub.access_until), "url": url},
        text=(
            f"La suscripción de {org.display_name} ({org.ruc}) ya no se renovará sola. "
            f"Sigues con acceso hasta el {_fecha(sub.access_until)}.\n\n{url}\n"
        ),
    )


def mes_gratis(reward: ReferralReward) -> None:
    user = reward.user
    empresa = None
    if reward.applied_to:
        org = reward.applied_to.organization
        empresa = f"{org.display_name} · {org.ruc}"
    url = f"{settings.FRONTEND_URL}/suscripcion"
    send_email(
        "¡Ganaste un mes gratis!", user.email, "mes_gratis",
        {"meta": reward.conversions_at_grant and int(getattr(settings, "REFERRAL_TARGET", 5)),
         "codigo": user.referral_code, "dias": reward.days, "empresa": empresa, "url": url},
        text=(
            f"Tus referidos ya pagan Empresario: te regalamos {reward.days} días"
            + (f", aplicados a {empresa}" if empresa else ", que se aplicarán cuando registres tu empresa")
            + f".\n\n{url}\n"
        ),
    )


def fin_de_prueba(sub: Subscription, user, dias: int) -> None:
    org = sub.organization
    url = f"{settings.FRONTEND_URL}/suscripcion"
    titulo = "Tu prueba termina mañana" if dias <= 1 else f"Tu prueba termina en {dias} días"
    send_email(
        titulo, user.email, "fin_de_prueba",
        {"titulo": titulo, "empresa": f"{org.display_name} · {org.ruc}", "hasta": _fecha(sub.trial_end), "url": url},
        text=(
            f"La prueba gratuita de {org.display_name} ({org.ruc}) termina el {_fecha(sub.trial_end)}. "
            f"Elige un plan para seguir sin cortes: {url}\n"
        ),
    )


def pago_manual(payment: Payment) -> None:
    to = getattr(settings, "BILLING_NOTIFY_EMAIL", "")
    if not to:
        return
    org = payment.subscription.organization
    send_email(
        f"Solicitud de pago · {org.ruc}", to, "pago_manual",
        {"empresa": f"{org.display_name} · {org.ruc}", "plan": payment.plan.name, "importe": _importe(payment),
         "solicitante": payment.created_by.email if payment.created_by else "—", "referencia": str(payment.pk),
         "url": f"{settings.API_PUBLIC_URL or settings.FRONTEND_URL}/admin/billing/payment/{payment.pk}/change/"},
        text=(
            f"Empresa: {org.display_name} ({org.ruc})\nPlan: {payment.plan.name} · {_importe(payment)}\n"
            f"Solicitado por: {payment.created_by.email if payment.created_by else '—'}\nPago: {payment.pk}\n"
        ),
    )
