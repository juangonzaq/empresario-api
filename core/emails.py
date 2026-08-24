"""Correos transaccionales en HTML, con texto plano de respaldo.

Cada correo se arma con una plantilla de contenido sobre ``email/base.html``
(marca, botón, pie) y lleva además la versión en texto: los clientes que no
pintan HTML —y los filtros de spam— ven el mismo mensaje. El enlace siempre
va visible también en texto: un correo que esconde adónde lleva parece
phishing, y aquí pedimos confianza.

Nada de aquí tumba la operación que lo originó: si el proveedor falla, se
registra y se sigue. Sin SMTP configurado, Django lo imprime en consola.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

BRAND = "EMPRESARIO"


def send_email(
    subject: str,
    to: str | list[str],
    template: str,
    context: dict,
    text: str,
) -> bool:
    """Envía ``email/<template>.html`` renderizado con ``context`` y ``text``
    como alternativa plana. Devuelve si se pudo entregar al backend."""
    recipients = [to] if isinstance(to, str) else list(to)
    ctx = {
        "subject": subject,
        "frontend_url": settings.FRONTEND_URL,
        "brand": BRAND,
        **context,
    }
    try:
        html = render_to_string(f"email/{template}.html", ctx)
        message = EmailMultiAlternatives(
            subject=f"{subject} · {BRAND}",
            body=text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=recipients,
        )
        message.attach_alternative(html, "text/html")
        message.send(fail_silently=False)
        return True
    except Exception:  # noqa: BLE001 — el envío no debe tumbar la operación
        logger.exception("No se pudo enviar el correo «%s» a %s", subject, recipients)
        return False
