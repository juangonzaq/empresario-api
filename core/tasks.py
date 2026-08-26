"""Entrega de correos en segundo plano.

La petición que origina un correo no debe esperar al SMTP ni caerse con él:
``core.emails.send_email`` renderiza el mensaje y lo encola aquí, donde un
fallo del proveedor se reintenta solo (1 min, 2 min, 4 min) en lugar de
perderse o colgar al usuario.
"""

from __future__ import annotations

from celery import shared_task


@shared_task(
    name="core.deliver_email",
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 3},
)
def deliver_email(subject: str, recipients: list[str], text: str, html: str) -> None:
    from core.emails import deliver

    deliver(subject, recipients, text, html)
