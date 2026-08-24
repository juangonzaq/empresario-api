"""Pasarelas de pago, detrás de una interfaz mínima.

* ``fake``: aprueba en el acto. Solo con ``DEBUG``: sirve para probar el flujo
  completo (prueba → plan → activo → referidos) sin tarjeta ni cuenta.
* ``manual``: deja el pago pendiente y avisa por correo; alguien lo aprueba en
  el admin. Es el modo seguro por defecto en producción hasta tener pasarela.
* ``mercadopago``: Checkout Pro. Se crea una preferencia, la persona paga en
  Mercado Pago y vuelve; el aviso de pago llega por webhook y se confirma
  consultando el pago por id antes de aprobar nada.
"""

from __future__ import annotations

import hashlib
import hmac
import logging

import requests
from django.conf import settings

from .models import Payment, PaymentStatus, Subscription

logger = logging.getLogger(__name__)

MP_API = "https://api.mercadopago.com"


def frontend(path: str, request=None) -> str:
    return f"{public_origin(request)}{path}"


def public_origin(request=None) -> str:
    """Desde dónde se está usando Empresario: el ``Origin`` de la petición
    (el túnel en desarrollo, el dominio en producción) y, si no viene,
    ``FRONTEND_URL``. Con el proxy de Next ese mismo origen sirve el API."""
    if request is not None:
        origin = request.headers.get("Origin") or ""
        if not origin:
            referer = request.headers.get("Referer") or ""
            if referer:
                from urllib.parse import urlsplit

                parts = urlsplit(referer)
                origin = f"{parts.scheme}://{parts.netloc}"
        if origin and origin != "null":
            return origin.rstrip("/")
    return settings.FRONTEND_URL.rstrip("/")


def api_public_url(request=None) -> str:
    return settings.API_PUBLIC_URL or public_origin(request)


class Gateway:
    name = "base"
    supports_recurring = False

    def create_checkout(self, payment: Payment, request=None) -> None:  # pragma: no cover
        raise NotImplementedError

    def create_subscription(self, payment: Payment, request=None) -> None:  # pragma: no cover
        raise NotImplementedError

    def cancel_subscription(self, sub: Subscription) -> None:  # pragma: no cover
        raise NotImplementedError


class FakeGateway(Gateway):
    name = "fake"
    supports_recurring = True

    def create_checkout(self, payment: Payment, request=None) -> None:
        from . import services

        if not settings.DEBUG:
            raise RuntimeError("La pasarela de prueba solo se permite con DEBUG=True.")
        payment.checkout_url = frontend("/suscripcion?estado=ok", request)
        payment.save(update_fields=["checkout_url", "updated_at"])
        services.approve_payment(payment, gateway_payment_id=f"fake-{payment.pk}", raw={"fake": True})

    def create_subscription(self, payment: Payment, request=None) -> None:
        """Simula el alta y el primer cobro en el acto."""
        from . import services

        if not settings.DEBUG:
            raise RuntimeError("La pasarela de prueba solo se permite con DEBUG=True.")
        sub = payment.subscription
        sub.gateway = self.name
        sub.gateway_subscription_id = f"fake-sub-{sub.pk}"
        sub.gateway_status = "authorized"
        sub.auto_renew = True
        sub.save(update_fields=["gateway", "gateway_subscription_id", "gateway_status", "auto_renew", "updated_at"])
        payment.checkout_url = frontend("/suscripcion?estado=ok", request)
        payment.save(update_fields=["checkout_url", "updated_at"])
        services.approve_payment(payment, gateway_payment_id=f"fake-{payment.pk}", raw={"fake": True})
        sub.refresh_from_db()
        sub.next_charge_at = sub.current_period_end
        sub.save(update_fields=["next_charge_at", "updated_at"])

    def cancel_subscription(self, sub: Subscription) -> None:
        return None


class ManualGateway(Gateway):
    name = "manual"

    def create_checkout(self, payment: Payment, request=None) -> None:
        from . import emails

        emails.pago_manual(payment)


class MercadoPagoGateway(Gateway):
    """Checkout Pro para pagos sueltos y **Suscripciones** (preapproval) para
    el cobro recurrente. En el recurrente la persona autoriza una vez su
    tarjeta y Mercado Pago cobra solo cada periodo; cada cobro llega por
    webhook y alarga la vigencia. Cancelar deja de cobrar; lo pagado sigue
    vigente hasta su fin."""

    name = "mercadopago"
    supports_recurring = True

    def __init__(self) -> None:
        self.token = getattr(settings, "MERCADOPAGO_ACCESS_TOKEN", "")
        if not self.token:
            raise RuntimeError("Falta MERCADOPAGO_ACCESS_TOKEN.")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def create_checkout(self, payment: Payment, request=None) -> None:
        org = payment.subscription.organization
        body = {
            "items": [{
                "id": payment.plan.code,
                "title": f"Empresario · {payment.plan.name} · {org.ruc}",
                "quantity": 1,
                "unit_price": float(payment.amount),
                "currency_id": payment.currency,
            }],
            "external_reference": str(payment.pk),
            "metadata": {"payment_id": str(payment.pk), "ruc": org.ruc},
            "back_urls": {
                "success": frontend("/suscripcion?estado=ok", request),
                "pending": frontend("/suscripcion?estado=pendiente", request),
                "failure": frontend("/suscripcion?estado=error", request),
            },
            "auto_return": "approved",
            "notification_url": f"{api_public_url(request)}/api/billing/webhook/mercadopago/",
            "statement_descriptor": "EMPRESARIO",
        }
        if payment.created_by:
            body["payer"] = {"email": payment.created_by.email}
        response = requests.post(
            f"{MP_API}/checkout/preferences", json=body, headers=self._headers(), timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        payment.gateway_reference = str(data.get("id", ""))
        payment.checkout_url = data.get("init_point") or data.get("sandbox_init_point") or ""
        payment.save(update_fields=["gateway_reference", "checkout_url", "updated_at"])

    # ------------------------------------------------------------ recurrente
    def create_subscription(self, payment: Payment, request=None) -> None:
        sub = payment.subscription
        org = sub.organization
        plan = payment.plan
        body = {
            "reason": f"Empresario · {plan.name} · {org.ruc}",
            # La referencia es la suscripción local: todos los cobros que
            # genere esta autorización vuelven con ella.
            "external_reference": str(sub.pk),
            "payer_email": payment.created_by.email if payment.created_by else "",
            "auto_recurring": {
                "frequency": plan.months,
                "frequency_type": "months",
                "transaction_amount": float(plan.price),
                "currency_id": plan.currency,
            },
            "back_url": frontend("/suscripcion?estado=ok", request),
            "status": "pending",
        }
        response = requests.post(f"{MP_API}/preapproval", json=body, headers=self._headers(), timeout=15)
        response.raise_for_status()
        data = response.json()
        sub.gateway = self.name
        sub.gateway_subscription_id = str(data.get("id", ""))
        sub.gateway_status = data.get("status", "pending")
        sub.save(update_fields=["gateway", "gateway_subscription_id", "gateway_status", "updated_at"])
        payment.gateway_reference = sub.gateway_subscription_id
        payment.checkout_url = data.get("init_point") or data.get("sandbox_init_point") or ""
        payment.save(update_fields=["gateway_reference", "checkout_url", "updated_at"])

    def cancel_subscription(self, sub: Subscription) -> None:
        response = requests.put(
            f"{MP_API}/preapproval/{sub.gateway_subscription_id}",
            json={"status": "cancelled"}, headers=self._headers(), timeout=15,
        )
        response.raise_for_status()

    def fetch_preapproval(self, preapproval_id: str) -> dict:
        response = requests.get(f"{MP_API}/preapproval/{preapproval_id}", headers=self._headers(), timeout=15)
        response.raise_for_status()
        return response.json()

    def fetch_authorized_payment(self, authorized_id: str) -> dict:
        response = requests.get(f"{MP_API}/authorized_payments/{authorized_id}", headers=self._headers(), timeout=15)
        response.raise_for_status()
        return response.json()

    def _sync_preapproval(self, info: dict) -> Subscription | None:
        sub = None
        ref = info.get("external_reference")
        if ref:
            sub = Subscription.objects.filter(pk=ref).first()
        if sub is None and info.get("id"):
            sub = Subscription.objects.filter(gateway_subscription_id=str(info["id"])).first()
        if sub is None:
            return None
        status = info.get("status", "")
        sub.gateway = self.name
        sub.gateway_subscription_id = str(info.get("id", sub.gateway_subscription_id))
        sub.gateway_status = status
        sub.auto_renew = status == "authorized"
        from django.utils.dateparse import parse_datetime

        nxt = info.get("next_payment_date")
        sub.next_charge_at = parse_datetime(nxt) if nxt else None
        sub.save(update_fields=["gateway", "gateway_subscription_id", "gateway_status", "auto_renew", "next_charge_at", "updated_at"])
        return sub

    def fetch_payment(self, mp_payment_id: str) -> dict:
        response = requests.get(
            f"{MP_API}/v1/payments/{mp_payment_id}", headers=self._headers(), timeout=15,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def signature_ok(request, data_id: str) -> bool:
        """Valida ``x-signature`` si hay secreto configurado; sin secreto, se
        confía en la consulta del pago por id (que ya exige nuestro token)."""
        secret = getattr(settings, "MERCADOPAGO_WEBHOOK_SECRET", "")
        if not secret:
            return True
        sig = request.headers.get("x-signature", "")
        parts = dict(p.split("=", 1) for p in sig.split(",") if "=" in p)
        ts, v1 = parts.get("ts"), parts.get("v1")
        if not ts or not v1:
            return False
        request_id = request.headers.get("x-request-id", "")
        manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
        expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, v1)

    def handle_notification(self, request) -> Payment | None:
        from . import services

        data_id = request.query_params.get("data.id") or request.query_params.get("id") or (
            (request.data or {}).get("data") or {}
        ).get("id")
        topic = request.query_params.get("type") or request.query_params.get("topic") or (request.data or {}).get("type")
        if not data_id:
            return None
        if not self.signature_ok(request, str(data_id)):
            logger.warning("Webhook de Mercado Pago con firma inválida (%s)", data_id)
            return None

        # Suscripción creada / autorizada / pausada / cancelada.
        if topic == "subscription_preapproval":
            self._sync_preapproval(self.fetch_preapproval(str(data_id)))
            return None
        # Cobro periódico programado por la suscripción.
        if topic == "subscription_authorized_payment":
            info = self.fetch_authorized_payment(str(data_id))
            pay = info.get("payment") or {}
            if pay.get("status") != "approved":
                return None
            sub = Subscription.objects.filter(gateway_subscription_id=str(info.get("preapproval_id", ""))).first()
            if sub is None and info.get("external_reference"):
                sub = Subscription.objects.filter(pk=info["external_reference"]).first()
            if sub is None:
                return None
            return services.record_recurring_charge(
                sub, amount=info.get("transaction_amount") or 0, currency=info.get("currency_id") or "PEN",
                gateway_payment_id=str(pay.get("id") or data_id), raw=info,
            )
        if topic and topic != "payment":
            return None

        info = self.fetch_payment(str(data_id))
        ref = info.get("external_reference")
        payment = Payment.objects.filter(pk=ref).first() if ref else None
        if payment is None:
            # Cobro de una suscripción: la referencia es la suscripción local
            # (o el id de la preapproval en metadata).
            sub = Subscription.objects.filter(pk=ref).first() if ref else None
            if sub is None:
                pre = (info.get("metadata") or {}).get("preapproval_id")
                if pre:
                    sub = Subscription.objects.filter(gateway_subscription_id=str(pre)).first()
            if sub is not None:
                if info.get("status") == "approved":
                    return services.record_recurring_charge(
                        sub, amount=info.get("transaction_amount") or 0,
                        currency=info.get("currency_id") or "PEN",
                        gateway_payment_id=str(info.get("id") or data_id), raw=info,
                    )
                return None
            logger.warning("Webhook de Mercado Pago sin pago local (ref=%s)", ref)
            return None
        status = info.get("status")
        if status == "approved":
            return services.approve_payment(payment, gateway_payment_id=str(data_id), raw=info)
        if status in {"rejected", "cancelled", "refunded", "charged_back"}:
            return services.reject_payment(
                payment,
                status=PaymentStatus.CANCELED if status != "rejected" else PaymentStatus.REJECTED,
                raw=info,
            )
        payment.raw = info
        payment.save(update_fields=["raw", "updated_at"])
        return payment


def get_gateway() -> Gateway:
    name = getattr(settings, "BILLING_GATEWAY", "manual")
    if name == "fake":
        return FakeGateway()
    if name == "mercadopago":
        return MercadoPagoGateway()
    return ManualGateway()
