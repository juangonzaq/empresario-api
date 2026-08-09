"""Correos transaccionales de la cuenta.

Se envían en texto plano y con el enlace visible: un correo de verificación
que esconde su destino detrás de un botón es indistinguible de un phishing, y
aquí justamente estamos pidiéndole al usuario que confíe en nosotros para
guardarle su clave SOL.

El envío nunca tumba la petición que lo originó: si el proveedor de correo
falla, se registra y la operación continúa. Registrarse y luego no poder
entrar porque el SMTP estaba caído sería peor que no recibir el correo.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail

from ..models import OneTimeToken, TokenPurpose, User

logger = logging.getLogger(__name__)

VERIFICATION_HOURS = 48
RESET_HOURS = 2


def _link(path: str, token: str) -> str:
    return f"{settings.FRONTEND_URL}{path}?token={token}"


def _send(subject: str, body: str, to: str) -> None:
    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[to],
            fail_silently=False,
        )
    except Exception:  # noqa: BLE001 — el envío no debe tumbar la operación
        logger.exception("No se pudo enviar el correo «%s» a %s", subject, to)


def send_verification(user: User) -> OneTimeToken:
    token = OneTimeToken.issue(
        user, TokenPurpose.EMAIL_VERIFICATION, hours=VERIFICATION_HOURS
    )
    _send(
        "Confirma tu correo · EMPRESARIO",
        (
            f"Hola{' ' + user.first_name if user.first_name else ''}:\n\n"
            "Confirma tu correo para terminar de crear tu cuenta:\n\n"
            f"{_link('/verificar-correo', token.token)}\n\n"
            f"El enlace vence en {VERIFICATION_HOURS} horas.\n"
            "Si no creaste esta cuenta, ignora este mensaje.\n"
        ),
        user.email,
    )
    return token


def send_password_reset(user: User) -> OneTimeToken:
    token = OneTimeToken.issue(user, TokenPurpose.PASSWORD_RESET, hours=RESET_HOURS)
    _send(
        "Recupera tu contraseña · EMPRESARIO",
        (
            "Pediste restablecer tu contraseña. Puedes hacerlo aquí:\n\n"
            f"{_link('/nueva-contrasena', token.token)}\n\n"
            f"El enlace vence en {RESET_HOURS} horas y solo se puede usar una vez.\n"
            "Si no fuiste tú, no hace falta que hagas nada: tu contraseña "
            "actual sigue siendo válida.\n"
        ),
        user.email,
    )
    return token


def send_password_changed(user: User) -> None:
    """Aviso de seguridad: si el cambio no fue suyo, quiere enterarse hoy."""
    _send(
        "Tu contraseña cambió · EMPRESARIO",
        (
            "La contraseña de tu cuenta acaba de cambiar.\n\n"
            "Si no fuiste tú, restablécela de inmediato desde "
            f"{settings.FRONTEND_URL}/recuperar y revisa quién tiene acceso a "
            "tu correo.\n"
        ),
        user.email,
    )
