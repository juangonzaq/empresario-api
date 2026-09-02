from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.permissions import BasePermission


class PaymentRequired(APIException):
    """La prueba terminó y la empresa no tiene un periodo pagado vigente.

    Lleva ``code`` para que el frontend lleve a la página de suscripción sin
    comparar textos."""

    status_code = status.HTTP_402_PAYMENT_REQUIRED
    default_detail = "La prueba gratuita terminó. Elige un plan para seguir usando Empresario."
    default_code = "suscripcion_vencida"

    def __init__(self, detail: str | None = None):
        super().__init__({"detail": detail or self.default_detail, "code": self.default_code})


class PaidPlanActive(BasePermission):
    """Funciones del plan de pago: la prueba gratuita no las incluye.

    Se suma DESPUÉS de ``SubscriptionActive`` en las vistas premium (IA):
    aquella deja pasar la prueba vigente; esta exige periodo pagado. El 402
    lleva el mismo ``code``, así el frontend aterriza en Suscripción igual.
    """

    def has_permission(self, request, view) -> bool:
        from .services import has_paid_plan

        organization = getattr(request, "organization", None)
        if organization is None:
            return True  # ya lo rechazó (o lo rechazará) HasOrganization
        if has_paid_plan(organization):
            return True
        raise PaymentRequired(
            "Esta función es parte del plan de pago. Actívalo en Suscripción."
        )


class SubscriptionActive(BasePermission):
    """Exige una suscripción vigente en la empresa ya resuelta por ``HasOrganization``."""

    def has_permission(self, request, view) -> bool:
        from .services import ensure_subscription

        organization = getattr(request, "organization", None)
        if organization is None:
            return True  # ya lo rechazó (o lo rechazará) HasOrganization
        if ensure_subscription(organization).is_active:
            return True
        raise PaymentRequired()
