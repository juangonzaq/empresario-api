"""Pasarelas de pago, detrás de una interfaz mínima.

* sin configurar (``BILLING_GATEWAY`` vacío): no se puede pagar. El checkout
  responde 503 y no se crea ningún pago ni se manda ningún correo.
* ``fake``: aprueba en el acto. Solo con ``DEBUG`` **y** pedida a mano en el
  ``.env``: sirve para probar el flujo completo (prueba → plan → activo →
  referidos) sin tarjeta ni cuenta. Nunca se activa sola.
* ``manual``: deja el pago pendiente y avisa por correo; alguien lo aprueba en
  el admin. Es el modo seguro por defecto en producción hasta tener pasarela.
* ``mercadopago``: Checkout Pro. Se crea una preferencia, la persona paga en
  Mercado Pago y vuelve; el aviso de pago llega por webhook y se confirma
  consultando el pago por id antes de aprobar nada.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import logging
import re

import requests
from django.conf import settings
from django.utils import timezone

from .models import PaymentKind, Payment, PaymentStatus, Subscription

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


class GatewayUnavailable(Exception):
    """No hay pasarela con la que cobrar. Se corta antes de crear nada."""


class Gateway:
    name = "base"
    supports_recurring = False

    def ensure_available(self) -> None:
        """Lanza ``GatewayUnavailable`` si con esta pasarela no se puede cobrar."""
        return None

    def create_checkout(self, payment: Payment, request=None) -> None:  # pragma: no cover
        raise NotImplementedError

    def create_subscription(self, payment: Payment, request=None) -> None:  # pragma: no cover
        raise NotImplementedError

    def cancel_subscription(self, sub: Subscription) -> None:  # pragma: no cover
        raise NotImplementedError

    def pause_subscription(self, sub: Subscription) -> None:
        """Deja de cobrar hasta `resume_subscription`. Sin pasarela recurrente, nada."""
        return None

    def update_amount(self, sub: Subscription, amount) -> None:
        """Cambia lo que se cobra en cada ciclo (plan + asientos adicionales)."""
        return None

    def resume_subscription(self, sub: Subscription) -> None:
        return None


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


class UnconfiguredGateway(Gateway):
    """Lo que hay cuando nadie configuró cobros: se niega a todo."""

    name = "none"
    MENSAJE = (
        "Los pagos todavía no están habilitados en esta instalación: falta "
        "configurar la pasarela de cobro."
    )

    def ensure_available(self) -> None:
        raise GatewayUnavailable(self.MENSAJE)

    def create_checkout(self, payment: Payment, request=None) -> None:
        raise GatewayUnavailable(self.MENSAJE)

    def create_subscription(self, payment: Payment, request=None) -> None:
        raise GatewayUnavailable(self.MENSAJE)

    def cancel_subscription(self, sub: Subscription) -> None:
        return None


class ManualGateway(Gateway):
    name = "manual"

    def create_checkout(self, payment: Payment, request=None) -> None:
        from . import emails

        emails.pago_manual(payment)


class MercadoPagoError(Exception):
    """Mercado Pago rechazó la petición; lleva el motivo que devolvió."""


def _mp_ok(response: requests.Response) -> dict:
    """`raise_for_status` con el motivo real de Mercado Pago en el mensaje.

    Un «400 Bad Request» a secas no dice si fue el correo del pagador, la URL
    de retorno o la moneda; el cuerpo de la respuesta sí."""
    if 200 <= response.status_code < 300:
        try:
            return response.json() or {}
        except ValueError:
            return {}
    try:
        data = response.json()
    except ValueError:
        data = {}
    motivo = data.get("message") or getattr(response, "text", "")[:300]
    causas = data.get("cause") or []
    if causas:
        motivo += " · " + "; ".join(
            str(c.get("description") or c.get("code") or c) for c in causas
        )
    logger.error("Mercado Pago %s en %s: %s", response.status_code, getattr(response, "url", "?"), motivo)
    raise MercadoPagoError(f"Mercado Pago respondió {response.status_code}: {motivo}")


def _exigir_origen_publico(request) -> None:
    """Mercado Pago rechaza `back_url`/`notification_url` que no sean URLs
    públicas (https, con dominio): con `http://localhost:3000` responde
    «Invalid value for back_url». Mejor decirlo antes, en palabras, que
    devolver un 502 a ciegas."""
    from urllib.parse import urlsplit

    origen = public_origin(request)
    partes = urlsplit(origen)
    host = partes.hostname or ""
    if partes.scheme != "https" or host in ("localhost", "127.0.0.1") or host.endswith(".local"):
        raise GatewayUnavailable(
            f"Mercado Pago necesita una dirección pública (https) para volver "
            f"después del pago y avisar del cobro, y ahora mismo estás en "
            f"{origen}. En desarrollo abre Empresario desde el túnel "
            f"(cloudflared) en lugar de localhost."
        )


_COLLECTOR_ES_DE_PRUEBA: dict[str, bool] = {}


def _collector_es_de_prueba(token: str) -> bool:
    """¿El dueño del token es un usuario de prueba de Mercado Pago?

    Solo se consulta para tokens `APP_USR-…` (los `TEST-…` son de la cuenta
    real) y una vez por proceso. Si MP no contesta, se asume que no."""
    if not token.startswith("APP_USR-"):
        return False
    if token not in _COLLECTOR_ES_DE_PRUEBA:
        try:
            me = requests.get(
                f"{MP_API}/users/me", headers={"Authorization": f"Bearer {token}"}, timeout=10,
            ).json()
            _COLLECTOR_ES_DE_PRUEBA[token] = "test_user" in (me.get("tags") or [])
        except Exception:  # noqa: BLE001 — sin red se sigue como cuenta real
            _COLLECTOR_ES_DE_PRUEBA[token] = False
    return _COLLECTOR_ES_DE_PRUEBA[token]


def _normalizar_correo_de_prueba(valor: str) -> str:
    """Acepta lo que el panel de Mercado Pago deja copiar.

    En «Cuentas de prueba» se copia el usuario (`TESTUSER9054…`), no el
    correo; y en un .env es fácil dejar comillas. El correo real de un usuario
    de prueba es `test_user_<dígitos>@testuser.com`, así que se deriva."""
    v = (valor or "").strip().strip('"').strip("'")
    m = re.fullmatch(r"(?i)testuser(\d+)", v)
    if m:
        return f"test_user_{m.group(1)}@testuser.com"
    return v


def _payer_email(payment: Payment) -> str:
    """Correo del pagador para Mercado Pago.

    MP rechaza mezclar: «Both payer and collector must be real or test users».
    Con un vendedor de prueba el pagador tiene que ser un comprador de prueba,
    y ese correo no es el del usuario de Empresario: viene de
    `MERCADOPAGO_TEST_PAYER_EMAIL`."""
    forzado = _normalizar_correo_de_prueba(getattr(settings, "MERCADOPAGO_TEST_PAYER_EMAIL", ""))
    if forzado:
        return forzado
    if _collector_es_de_prueba(settings.MERCADOPAGO_ACCESS_TOKEN):
        raise GatewayUnavailable(
            "El vendedor configurado en Mercado Pago es un usuario de prueba, y "
            "Mercado Pago solo le acepta pagos de compradores de prueba. Define "
            "MERCADOPAGO_TEST_PAYER_EMAIL en el .env con el correo del comprador "
            "de prueba (test_user_…@testuser.com) y reinicia el API."
        )
    return payment.created_by.email if payment.created_by else ""


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
        _exigir_origen_publico(request)
        org = payment.subscription.organization
        body = {
            "items": [{
                "id": payment.plan.code,
                # Sin el RUC en el texto visible: el sanitizador de Mercado
                # Pago toma los 11 dígitos por un documento y reemplaza TODO el
                # título por «[REDACTED]». El RUC ya viaja en metadata.
                "title": f"Empresario · {payment.plan.name}",
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
        email = _payer_email(payment)
        if email:
            body["payer"] = {"email": email}
        response = requests.post(
            f"{MP_API}/checkout/preferences", json=body, headers=self._headers(), timeout=15,
        )
        data = _mp_ok(response)
        payment.gateway_reference = str(data.get("id", ""))
        payment.checkout_url = data.get("init_point") or data.get("sandbox_init_point") or ""
        payment.save(update_fields=["gateway_reference", "checkout_url", "updated_at"])

    # ------------------------------------------------------------ recurrente
    def create_subscription(self, payment: Payment, request=None) -> None:
        _exigir_origen_publico(request)
        sub = payment.subscription
        org = sub.organization
        plan = payment.plan
        auto_recurring = {
            "frequency": plan.months,
            "frequency_type": "months",
            "transaction_amount": float(plan.price),
            "currency_id": plan.currency,
        }
        # Lo ya pagado no se cobra dos veces: al cambiar de plan con un periodo
        # pagado vigente, el primer cobro del nuevo se programa para cuando
        # termine (sin esto, pasar de anual a mensual con un año pagado volvía
        # a cobrar hoy). La prueba gratis, en cambio, no difiere nada: se cobra
        # al autorizar y el periodo pagado empieza igual cuando acabe la prueba
        # (`approve_payment`), así que esos días no se pierden. Cobrar de una
        # vez evita además el cargo de validación de S/ 2 y el hueco entre el
        # fin de la prueba y la hora a la que MP cobra.
        pagado_hasta = sub.current_period_end
        if pagado_hasta and pagado_hasta > timezone.now() + datetime.timedelta(minutes=5):
            auto_recurring["start_date"] = pagado_hasta.isoformat(timespec="milliseconds")
        body = {
            # Sin RUC por la misma censura de Mercado Pago que en el título
            # del checkout: con 11 dígitos, el nombre llega como «[REDACTED]».
            "reason": f"Empresario · {plan.name}",
            # La referencia es la suscripción local: todos los cobros que
            # genere esta autorización vuelven con ella.
            "external_reference": str(sub.pk),
            "payer_email": _payer_email(payment),
            "auto_recurring": auto_recurring,
            # Sin query string: Mercado Pago le añade «?preapproval_id=…» tal
            # cual, y con «?estado=ok» delante quedaba «?estado=ok?preapproval_id».
            # El front entiende `preapproval_id` como vuelta correcta.
            "back_url": frontend("/suscripcion", request),
            "status": "pending",
        }
        response = requests.post(f"{MP_API}/preapproval", json=body, headers=self._headers(), timeout=15)
        data = _mp_ok(response)
        nueva = str(data.get("id", ""))
        # La suscripción vigente NO se reemplaza aquí: la nueva preapproval
        # solo existe en el pago (gateway_reference) hasta que MP la autorice
        # (webhook → `_sync_preapproval`), que es cuando se adopta y se
        # cancela la anterior. Si la persona abandona el checkout, la que
        # tenía sigue intacta y los asientos/pausas siguen apuntando a ella.
        if not sub.gateway_subscription_id:
            sub.gateway = self.name
            sub.gateway_subscription_id = nueva
            sub.gateway_status = data.get("status", "pending")
            sub.save(update_fields=["gateway", "gateway_subscription_id", "gateway_status", "updated_at"])
        payment.gateway_reference = nueva
        payment.checkout_url = data.get("init_point") or data.get("sandbox_init_point") or ""
        payment.save(update_fields=["gateway_reference", "checkout_url", "updated_at"])

    def _cancelar_preapproval(self, preapproval_id: str, *, reemplazada_por: str) -> None:
        """Al autorizarse una suscripción nueva, cancela en MP la que reemplaza."""
        try:
            _mp_ok(requests.put(
                f"{MP_API}/preapproval/{preapproval_id}", json={"status": "cancelled"},
                headers=self._headers(), timeout=15,
            ))
            logger.info("Suscripción MP %s cancelada: la reemplaza %s", preapproval_id, reemplazada_por)
        except Exception:  # noqa: BLE001 — ya cancelada o MP caído: queda en el log
            logger.exception("No se pudo cancelar la suscripción MP reemplazada %s", preapproval_id)

    def cancel_subscription(self, sub: Subscription) -> None:
        response = requests.put(
            f"{MP_API}/preapproval/{sub.gateway_subscription_id}",
            json={"status": "cancelled"}, headers=self._headers(), timeout=15,
        )
        _mp_ok(response)

    def update_amount(self, sub: Subscription, amount) -> None:
        _mp_ok(requests.put(
            f"{MP_API}/preapproval/{sub.gateway_subscription_id}",
            json={"auto_recurring": {
                "transaction_amount": float(amount),
                "currency_id": (sub.plan.currency if sub.plan else "PEN"),
            }},
            headers=self._headers(), timeout=15,
        ))

    # Mercado Pago no deja mover la fecha del próximo cobro; sí pausar y
    # reanudar. Un mes gratis = pausada un mes y reanudada (cobra al reanudar).
    def pause_subscription(self, sub: Subscription) -> None:
        _mp_ok(requests.put(
            f"{MP_API}/preapproval/{sub.gateway_subscription_id}",
            json={"status": "paused"}, headers=self._headers(), timeout=15,
        ))

    def resume_subscription(self, sub: Subscription) -> None:
        _mp_ok(requests.put(
            f"{MP_API}/preapproval/{sub.gateway_subscription_id}",
            json={"status": "authorized"}, headers=self._headers(), timeout=15,
        ))

    def fetch_preapproval(self, preapproval_id: str) -> dict:
        response = requests.get(f"{MP_API}/preapproval/{preapproval_id}", headers=self._headers(), timeout=15)
        return _mp_ok(response)

    def fetch_authorized_payment(self, authorized_id: str) -> dict:
        response = requests.get(f"{MP_API}/authorized_payments/{authorized_id}", headers=self._headers(), timeout=15)
        return _mp_ok(response)

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
        llegada = str(info.get("id", ""))
        vigente = sub.gateway_subscription_id
        if llegada and vigente and llegada != vigente:
            if status != "authorized":
                # Un intento que nunca se completó (o uno viejo ya cancelado):
                # no dice nada de la suscripción vigente.
                return sub
            # Cambio de plan autorizado: la nueva manda y la anterior se cancela
            # para que nunca cobren dos a la vez.
            self._cancelar_preapproval(vigente, reemplazada_por=llegada)
        sub.gateway = self.name
        sub.gateway_subscription_id = llegada or vigente
        sub.gateway_status = status
        # Pausada por un mes gratis sigue siendo una renovación viva: se
        # reanuda sola en `paused_until`. Pausada por otra vía, no.
        sub.auto_renew = status == "authorized" or (status == "paused" and sub.paused_until is not None)
        from django.utils.dateparse import parse_datetime

        nxt = info.get("next_payment_date")
        sub.next_charge_at = parse_datetime(nxt) if nxt else None
        sub.save(update_fields=["gateway", "gateway_subscription_id", "gateway_status", "auto_renew", "next_charge_at", "updated_at"])
        return sub

    def fetch_payment(self, mp_payment_id: str) -> dict:
        response = requests.get(
            f"{MP_API}/v1/payments/{mp_payment_id}", headers=self._headers(), timeout=15,
        )
        _mp_ok(response)
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
    name = getattr(settings, "BILLING_GATEWAY", "") or ""
    if name == "fake":
        # Fuera de DEBUG una pasarela que aprueba todo es un agujero, no un modo.
        if not settings.DEBUG:
            logger.error("BILLING_GATEWAY=fake ignorada: solo se permite con DEBUG")
            return UnconfiguredGateway()
        return FakeGateway()
    if name == "mercadopago":
        if not getattr(settings, "MERCADOPAGO_ACCESS_TOKEN", ""):
            logger.error("BILLING_GATEWAY=mercadopago sin MERCADOPAGO_ACCESS_TOKEN")
            return UnconfiguredGateway()
        return MercadoPagoGateway()
    if name == "manual":
        return ManualGateway()
    if name:
        logger.error("BILLING_GATEWAY=%r desconocida", name)
    return UnconfiguredGateway()
