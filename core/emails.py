"""Correos transaccionales en HTML, con texto plano de respaldo.

Cada correo se arma con una plantilla de contenido sobre ``email/base.html``
(marca, botón, pie) y lleva además la versión en texto: los clientes que no
pintan HTML —y los filtros de spam— ven el mismo mensaje. El enlace siempre
va visible también en texto: un correo que esconde adónde lleva parece
phishing, y aquí pedimos confianza.

Nada de aquí tumba la operación que lo originó: el mensaje se renderiza en la
petición (ahí están los modelos del contexto) pero la entrega va encolada a
Celery, que reintenta solo si el proveedor falla. Con el broker caído se
entrega en línea antes que perder el correo. Sin SMTP configurado, Django lo
imprime en consola.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

BRAND = "EMPRESARIO"


def deliver(subject: str, recipients: list[str], text: str, html: str) -> None:
    """Entrega al backend de correo, con error si no se pudo (para que la
    tarea que encola pueda reintentar)."""
    message = EmailMultiAlternatives(
        subject=subject,
        body=text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=recipients,
    )
    message.attach_alternative(html, "text/html")
    message.send(fail_silently=False)


def send_email(
    subject: str,
    to: str | list[str],
    template: str,
    context: dict,
    text: str,
) -> bool:
    """Renderiza ``email/<template>.html`` con ``context`` y encola su envío,
    con ``text`` como alternativa plana. Devuelve si quedó encolado (o, con el
    broker caído, entregado en línea)."""
    recipients = [to] if isinstance(to, str) else list(to)
    ctx = {
        "subject": subject,
        "frontend_url": settings.FRONTEND_URL,
        "brand": BRAND,
        **context,
    }
    try:
        html = render_to_string(f"email/{template}.html", ctx)
    except Exception:  # noqa: BLE001 — el envío no debe tumbar la operación
        logger.exception("No se pudo renderizar el correo «%s» a %s", subject, recipients)
        return False

    full_subject = f"{subject} · {BRAND}"
    if getattr(settings, "TESTING", False):
        # Los tests afirman sobre el outbox en la misma petición; encolar los
        # mandaría a un broker que no existe ahí.
        deliver(full_subject, recipients, text, html)
        return True
    try:
        from core.tasks import deliver_email

        deliver_email.delay(full_subject, recipients, text, html)
        return True
    except Exception:  # noqa: BLE001 — broker caído: mejor ahora que nunca
        logger.warning(
            "No se pudo encolar el correo «%s»; se entrega en línea.", subject
        )
        try:
            deliver(full_subject, recipients, text, html)
            return True
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo enviar el correo «%s» a %s", subject, recipients)
            return False
