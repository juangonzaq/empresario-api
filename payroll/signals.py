"""Salary history tracking (spec §3.4).

``Colaborador.monthly_salary`` holds only the current value; the annual
income-tax projection needs to know what the person earned in January when
it recalculates in August. Every change is recorded here automatically, so
no screen has to remember to do it.
"""

from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from colaboradores.models import Colaborador

from .models import EmployeeSalaryHistory


@receiver(post_save, sender=Colaborador)
def record_salary_change(sender, instance: Colaborador, **kwargs) -> None:
    if instance.monthly_salary is None:
        return
    last = (
        EmployeeSalaryHistory.objects.filter(colaborador=instance)
        .order_by("-effective_from", "-created_at")
        .first()
    )
    if last is not None and last.amount == instance.monthly_salary:
        return
    EmployeeSalaryHistory.objects.create(
        colaborador=instance,
        amount=instance.monthly_salary,
        effective_from=timezone.localdate(),
        reason=f"origen: {instance.salary_source}",
    )
