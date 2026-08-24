"""API de suscripción, pagos y referidos.

Estas vistas no llevan la puerta de suscripción: son justamente las que hacen
falta para pagar cuando la prueba terminó."""

from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.tenancy import CanManageOrganization, HasOrganization

from . import services
from .gateways import MercadoPagoGateway, get_gateway
from .models import Payment, PaymentStatus, Plan
from .serializers import (
    CheckoutSerializer, PaymentSerializer, PlanSerializer, UsageChargeSerializer,
)

logger = logging.getLogger(__name__)


def _plans_payload() -> list[dict]:
    plans = list(Plan.objects.filter(is_active=True))
    monthly = next((p for p in plans if p.months == 1), None)
    return PlanSerializer(plans, many=True, context={"monthly": monthly}).data


class PlansView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        return Response({"plans": _plans_payload(), "trial_days": services.trial_days()})


class SubscriptionView(APIView):
    permission_classes = [IsAuthenticated, HasOrganization]

    def get(self, request: Request) -> Response:
        data = services.summary(request.organization)
        sub = request.organization.subscription
        plan = PlanSerializer(sub.plan).data if sub.plan else None
        return Response({
            **data,
            "plan_detail": plan,
            "bonus_days": sub.bonus_days,
            "can_manage": request.membership.can_manage,
            "gateway": get_gateway().name,
            "supports_recurring": get_gateway().supports_recurring,
            "gateway_status": sub.gateway_status,
            "plans": _plans_payload(),
            "trial_days": services.trial_days(),
        })


class CheckoutView(APIView):
    permission_classes = [IsAuthenticated, CanManageOrganization]

    def post(self, request: Request) -> Response:
        serializer = CheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = serializer.validated_data["plan"]
        try:
            payment = services.start_checkout(request.organization, plan, request.user, request)
        except Exception as exc:  # noqa: BLE001 — la pasarela es un tercero
            logger.exception("No se pudo iniciar el pago para %s", request.ruc)
            return Response(
                {"detail": f"No pudimos iniciar el pago: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        payment.refresh_from_db()
        return Response({
            "payment": PaymentSerializer(payment).data,
            "checkout_url": payment.checkout_url or None,
            "subscription": services.summary(request.organization),
        }, status=status.HTTP_201_CREATED)


class CancelAutoRenewView(APIView):
    """Deja de cobrar automáticamente. Lo pagado sigue vigente hasta su fin."""

    permission_classes = [IsAuthenticated, CanManageOrganization]

    def post(self, request: Request) -> Response:
        try:
            services.cancel_auto_renew(request.organization, request.user)
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo cancelar la renovación de %s", request.ruc)
            return Response({"detail": f"No pudimos cancelar la renovación: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(services.summary(request.organization))


class PaymentsView(APIView):
    permission_classes = [IsAuthenticated, HasOrganization]

    def get(self, request: Request) -> Response:
        sub = services.ensure_subscription(request.organization)
        payments = Payment.objects.filter(subscription=sub).select_related("plan")[:50]
        return Response(PaymentSerializer(payments, many=True).data)


class ChargesView(APIView):
    """Cargos por uso de la empresa (p. ej. sincronizaciones manuales extra),
    para listarlos en la facturación."""

    permission_classes = [IsAuthenticated, HasOrganization]

    def get(self, request: Request) -> Response:
        charges = services.usage_charges(request.organization)
        return Response(UsageChargeSerializer(charges, many=True).data)


class ReferralsView(APIView):
    """Mi código, mi enlace y cómo voy. Es de la persona, no de la empresa."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(services.referral_summary(request.user))


class MercadoPagoWebhookView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    def post(self, request: Request) -> Response:
        return self._handle(request)

    def get(self, request: Request) -> Response:
        return self._handle(request)

    def _handle(self, request: Request) -> Response:
        try:
            gateway = MercadoPagoGateway()
        except RuntimeError:
            return Response({"detail": "Mercado Pago no está configurado."}, status=status.HTTP_404_NOT_FOUND)
        try:
            payment = gateway.handle_notification(request)
        except Exception:  # noqa: BLE001
            logger.exception("Error procesando webhook de Mercado Pago")
            # 200 igual: Mercado Pago reintenta ante 5xx y el error ya quedó en el log.
            return Response({"ok": False})
        return Response({"ok": True, "status": payment.status if payment else None})


# Para que el admin pueda aprobar un pago manual sin tocar la base.
def approve_manual(payment: Payment) -> Payment:
    if payment.status != PaymentStatus.APPROVED:
        return services.approve_payment(payment, gateway_payment_id="manual")
    return payment
