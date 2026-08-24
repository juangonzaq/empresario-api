from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.models import Organization

from .services import ensure_subscription


@receiver(post_save, sender=Organization)
def empresa_nueva_en_prueba(sender, instance: Organization, created: bool, **kwargs) -> None:
    if created:
        ensure_subscription(instance)
