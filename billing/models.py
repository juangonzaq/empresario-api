"""Planes, suscripciones, pagos y referidos.

La unidad que paga es la **empresa** (``Organization``): cada RUC tiene su
suscripción, que nace en prueba gratuita al crearse y pasa a pagada cuando se
aprueba un pago. El código de referido, en cambio, es de la **persona**: se
entrega al registrarse y cuenta las empresas de sus referidos que llegan a
pagar. Cada cinco, un mes gratis que se aplica a la suscripción de su propia
empresa.

Las fechas mandan, no los estados: una suscripción está vigente mientras
``access_until`` (lo último entre el fin de la prueba y el fin del periodo
pagado, más los días de regalo) sea futuro. Así no hay cron que «expire» nada
ni estados que se queden desfasados.
"""

from __future__ import annotations

import datetime
import math
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import BaseModel


class PlanInterval(models.TextChoices):
    MONTH = "month", "Mensual"
    YEAR = "year", "Anual"


class Plan(BaseModel):
    code = models.SlugField("código", max_length=30, unique=True)
    name = models.CharField("nombre", max_length=60)
    description = models.CharField("descripción", max_length=200, blank=True)
    price = models.DecimalField("precio", max_digits=10, decimal_places=2)
    currency = models.CharField("moneda", max_length=3, default="PEN")
    interval = models.CharField(max_length=10, choices=PlanInterval, default=PlanInterval.MONTH)
    # Cobro recurrente: la pasarela vuelve a cobrar sola cada periodo hasta
    # que la empresa cancele. Apagado, es un pago suelto por periodo.
    recurring = models.BooleanField("cobro recurrente", default=True)
    is_active = models.BooleanField("a la venta", default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    # Cuántas empresas puede administrar el titular con este plan antes de
    # necesitar asientos adicionales. El titular puede tener extras por encima
    # de esto (ver ``accounts.User.extra_company_seats``).
    included_company_seats = models.PositiveSmallIntegerField(
        "empresas incluidas", default=3,
    )
    # Asientos: lo que incluye el plan y lo que cuesta cada uno de más, al
    # mes. Los precios se editan en el admin; el fixture trae la base.
    included_member_seats = models.PositiveSmallIntegerField(
        "personas incluidas por empresa", default=3,
    )
    extra_member_seat_price = models.DecimalField(
        "precio por persona adicional (mes)", max_digits=10, decimal_places=2, default=Decimal("9.00"),
    )
    extra_company_seat_price = models.DecimalField(
        "precio por empresa adicional (mes)", max_digits=10, decimal_places=2, default=Decimal("9.00"),
    )

    class Meta:
        ordering = ["sort_order", "price"]
        verbose_name = "plan"
        verbose_name_plural = "planes"

    def __str__(self) -> str:
        return f"{self.name} · {self.currency} {self.price}"

    @property
    def months(self) -> int:
        return 12 if self.interval == PlanInterval.YEAR else 1

    @property
    def monthly_equivalent(self) -> Decimal:
        return (self.price / self.months).quantize(Decimal("0.01"))


class Subscription(BaseModel):
    organization = models.OneToOneField(
        "accounts.Organization", related_name="subscription", on_delete=models.CASCADE,
    )
    plan = models.ForeignKey(Plan, null=True, blank=True, on_delete=models.PROTECT)
    trial_end = models.DateTimeField("fin de la prueba")
    current_period_end = models.DateTimeField("fin del periodo pagado", null=True, blank=True)
    bonus_days = models.PositiveIntegerField("días de regalo acumulados", default=0)
    canceled_at = models.DateTimeField(null=True, blank=True)
    # Renovación automática en la pasarela (preapproval de Mercado Pago).
    gateway = models.CharField("pasarela", max_length=20, blank=True)
    gateway_subscription_id = models.CharField("id de suscripción en la pasarela", max_length=120, blank=True)
    gateway_status = models.CharField("estado en la pasarela", max_length=20, blank=True)
    auto_renew = models.BooleanField("renovación automática", default=False)
    next_charge_at = models.DateTimeField("próximo cobro", null=True, blank=True)
    # Mes gratis por referidos con renovación automática: la suscripción queda
    # pausada en la pasarela hasta esta fecha, en que se reanuda (y cobra).
    paused_until = models.DateTimeField("pausada en la pasarela hasta", null=True, blank=True)
    # Asientos adicionales contratados (add-ons recurrentes de esta suscripción).
    extra_member_seats = models.PositiveSmallIntegerField("personas adicionales", default=0)
    extra_company_seats = models.PositiveSmallIntegerField("empresas adicionales", default=0)
    trial_reminder_sent_at = models.DateTimeField("aviso de fin de prueba enviado", null=True, blank=True)

    class Meta:
        verbose_name = "suscripción"
        verbose_name_plural = "suscripciones"

    def __str__(self) -> str:
        return f"{self.organization.ruc} · {self.status}"

    @property
    def access_until(self) -> datetime.datetime:
        ends = [self.trial_end]
        if self.current_period_end:
            ends.append(self.current_period_end)
        return max(ends)

    @property
    def is_active(self) -> bool:
        return self.access_until > timezone.now()

    @property
    def status(self) -> str:
        now = timezone.now()
        if self.current_period_end and self.current_period_end > now:
            return "active"
        if self.trial_end > now:
            return "trialing"
        return "expired"

    @property
    def days_left(self) -> int:
        delta = self.access_until - timezone.now()
        return max(0, math.ceil(delta.total_seconds() / 86400))

    def extend(self, days: int) -> None:
        """Alarga la vigencia desde lo que ya tenía (o desde hoy, si venció)."""
        base = max(self.access_until, timezone.now())
        new_end = base + datetime.timedelta(days=days)
        if self.current_period_end and self.current_period_end >= self.trial_end:
            self.current_period_end = new_end
        else:
            self.trial_end = new_end
        self.bonus_days += days


class PaymentStatus(models.TextChoices):
    PENDING = "pending", "Pendiente"
    APPROVED = "approved", "Aprobado"
    REJECTED = "rejected", "Rechazado"
    CANCELED = "canceled", "Cancelado"


class PaymentKind(models.TextChoices):
    ONE_OFF = "one_off", "Pago único"
    RECURRING_SETUP = "recurring_setup", "Alta de suscripción"
    RECURRING_CHARGE = "recurring_charge", "Cobro recurrente"


class Payment(BaseModel):
    subscription = models.ForeignKey(Subscription, related_name="payments", on_delete=models.CASCADE)
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="PEN")
    status = models.CharField(max_length=10, choices=PaymentStatus, default=PaymentStatus.PENDING)
    kind = models.CharField(max_length=20, choices=PaymentKind, default=PaymentKind.ONE_OFF)
    gateway = models.CharField("pasarela", max_length=20)
    gateway_reference = models.CharField("referencia en la pasarela", max_length=120, blank=True)
    gateway_payment_id = models.CharField("id de pago en la pasarela", max_length=120, blank=True)
    checkout_url = models.URLField(max_length=500, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    period_start = models.DateTimeField(null=True, blank=True)
    period_end = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="payments_made",
    )
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "pago"
        verbose_name_plural = "pagos"

    def __str__(self) -> str:
        return f"{self.subscription.organization.ruc} · {self.plan.code} · {self.status}"


class Referral(BaseModel):
    """Quién trajo a quién. Se convierte cuando alguna empresa del referido paga."""

    referrer = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="referrals", on_delete=models.CASCADE)
    referred = models.OneToOneField(settings.AUTH_USER_MODEL, related_name="referral", on_delete=models.CASCADE)
    converted_at = models.DateTimeField(null=True, blank=True)
    converted_by = models.ForeignKey(Payment, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        verbose_name = "referido"
        verbose_name_plural = "referidos"

    def __str__(self) -> str:
        return f"{self.referrer.email} → {self.referred.email}"


class ReferralReward(BaseModel):
    """Un mes gratis ganado. Se aplica a la suscripción de la empresa del
    referente; si aún no tiene empresa, queda pendiente hasta que la cree."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="referral_rewards", on_delete=models.CASCADE)
    days = models.PositiveIntegerField(default=30)
    conversions_at_grant = models.PositiveIntegerField()
    applied_to = models.ForeignKey(Subscription, null=True, blank=True, on_delete=models.SET_NULL)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "premio por referidos"
        verbose_name_plural = "premios por referidos"


class UsageChargeKind(models.TextChoices):
    EXTRA_MANUAL_SYNC = "extra_manual_sync", "Sincronización manual adicional"


class UsageChargeStatus(models.TextChoices):
    PENDING = "pending", "Por cobrar"
    SETTLED = "settled", "Cobrado"
    WAIVED = "waived", "Exonerado"


class UsageCharge(BaseModel):
    """Un cargo por uso, fuera del plan: hoy, cada sincronización manual por
    encima del tope diario. Se registra (no cobra al instante) y se lista en la
    facturación de la empresa para liquidarse después."""

    organization = models.ForeignKey(
        "accounts.Organization", related_name="usage_charges", on_delete=models.CASCADE,
    )
    kind = models.CharField(max_length=30, choices=UsageChargeKind)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="PEN")
    description = models.CharField(max_length=200, blank=True)
    # Rastro de qué originó el cargo (p. ej. el id del SyncJob).
    reference = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=10, choices=UsageChargeStatus, default=UsageChargeStatus.PENDING)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="usage_charges_made",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "cargo por uso"
        verbose_name_plural = "cargos por uso"
        indexes = [models.Index(fields=["organization", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.organization.ruc} · {self.kind} · {self.currency} {self.amount}"
