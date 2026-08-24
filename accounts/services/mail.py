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

from core.emails import send_email

from ..models import OneTimeToken, TokenPurpose, User

logger = logging.getLogger(__name__)

VERIFICATION_HOURS = 48
RESET_HOURS = 2


def _link(path: str, token: str) -> str:
    return f"{settings.FRONTEND_URL}{path}?token={token}"


def _send(subject: str, body: str, to: str, template: str, context: dict) -> None:
    """HTML con texto plano de respaldo; el enlace va visible en los dos."""
    send_email(subject, to, template, context, text=body)


def send_verification(user: User) -> OneTimeToken:
    token = OneTimeToken.issue(
        user, TokenPurpose.EMAIL_VERIFICATION, hours=VERIFICATION_HOURS
    )
    url = _link('/verificar-correo', token.token)
    _send(
        "Confirma tu correo",
        (
            f"Hola{' ' + user.first_name if user.first_name else ''}:\n\n"
            "Confirma tu correo para terminar de crear tu cuenta:\n\n"
            f"{url}\n\n"
            f"El enlace vence en {VERIFICATION_HOURS} horas.\n"
            "Si no creaste esta cuenta, ignora este mensaje.\n"
        ),
        user.email,
        "verificacion",
        {"url": url, "nombre": user.first_name, "horas": VERIFICATION_HOURS},
    )
    return token


def send_password_reset(user: User) -> OneTimeToken:
    token = OneTimeToken.issue(user, TokenPurpose.PASSWORD_RESET, hours=RESET_HOURS)
    url = _link('/nueva-contrasena', token.token)
    _send(
        "Recupera tu contraseña",
        (
            "Pediste restablecer tu contraseña. Puedes hacerlo aquí:\n\n"
            f"{url}\n\n"
            f"El enlace vence en {RESET_HOURS} horas y solo se puede usar una vez.\n"
            "Si no fuiste tú, no hace falta que hagas nada: tu contraseña "
            "actual sigue siendo válida.\n"
        ),
        user.email,
        "recuperar",
        {"url": url, "horas": RESET_HOURS},
    )
    return token


def send_password_changed(user: User) -> None:
    """Aviso de seguridad: si el cambio no fue suyo, quiere enterarse hoy."""
    _send(
        "Tu contraseña cambió",
        (
            "La contraseña de tu cuenta acaba de cambiar.\n\n"
            "Si no fuiste tú, restablécela de inmediato desde "
            f"{settings.FRONTEND_URL}/recuperar y revisa quién tiene acceso a "
            "tu correo.\n"
        ),
        user.email,
        "clave_cambiada",
        {"url": f"{settings.FRONTEND_URL}/recuperar"},
    )
