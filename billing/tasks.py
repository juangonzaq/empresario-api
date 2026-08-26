"""Tareas periódicas de suscripciones."""

from __future__ import annotations

import datetime
import logging

from celery import shared_task
from django.utils import timezone

from accounts.models import Membership, Role

from . import emails
from .models import Subscription

logger = logging.getLogger(__name__)

AVISO_DIAS = 2


@shared_task(name="billing.reanudar_suscripciones_pausadas")
def reanudar_suscripciones_pausadas() -> int:
    """Cada hora: las suscripciones pausadas por un mes gratis cuyo plazo ya
    pasó se reanudan en la pasarela (que cobra al reanudar)."""
    from .services import reanudar_suscripciones_pausadas as reanudar

    n = reanudar()
    if n:
        logger.info("Suscripciones reanudadas tras su mes gratis: %d", n)
    return n


@shared_task(name="billing.avisar_fin_de_prueba")
def avisar_fin_de_prueba() -> int:
    """Una vez al día: a las empresas en prueba que terminan en ≤ 2 días, sin
    plan pagado y sin aviso previo, se les escribe a titular y contador."""
    now = timezone.now()
    limite = now + datetime.timedelta(days=AVISO_DIAS)
    pendientes = Subscription.objects.filter(
        trial_end__gt=now, trial_end__lte=limite,
        current_period_end__isnull=True, trial_reminder_sent_at__isnull=True,
    ).select_related("organization")
    enviados = 0
    for sub in pendientes:
        dias = max(1, (sub.trial_end - now).days + (1 if (sub.trial_end - now).seconds else 0))
        destinatarios = Membership.objects.filter(
            organization=sub.organization, is_active=True, role__in=[Role.OWNER, Role.ACCOUNTANT],
        ).select_related("user")
        for m in destinatarios:
            emails.fin_de_prueba(sub, m.user, dias)
            enviados += 1
        sub.trial_reminder_sent_at = now
        sub.save(update_fields=["trial_reminder_sent_at", "updated_at"])
    logger.info("Avisos de fin de prueba enviados: %d", enviados)
    return enviados
