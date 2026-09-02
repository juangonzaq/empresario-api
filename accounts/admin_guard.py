"""Doble puerta del /admin: reCAPTCHA + código de un solo uso al correo.

El admin ve TODAS las empresas del SaaS, así que su login no puede depender
solo de una contraseña: el reCAPTCHA frena bots y fuerza bruta, y el código
de 6 dígitos al correo del staff frena una contraseña filtrada. La vista
sombrea ``/admin/login/`` desde ``config/urls.py`` — el admin queda intacto y
sus redirecciones aterrizan aquí solas.

Sin llaves de reCAPTCHA configuradas (desarrollo) el captcha se omite; el
código por correo aplica siempre.
"""

from __future__ import annotations

import hmac
import logging
import secrets
from datetime import timedelta

import requests
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth import login as auth_login
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache

from core.emails import send_email

from .models import OneTimeToken, TokenPurpose, User

logger = logging.getLogger(__name__)

OTP_MINUTOS = 10
MAX_INTENTOS = 5
# reCAPTCHA v3: sin casilla, devuelve un puntaje 0–1. Bajo este umbral se
# trata como bot. La acción amarra el token a ESTE formulario: un token v3
# emitido en otra página del sitio no sirve aquí.
CAPTCHA_ACCION = "admin_login"
CAPTCHA_PUNTAJE_MIN = 0.5
SESION_USER = "admin_otp_user"
SESION_INTENTOS = "admin_otp_intentos"
SESION_NEXT = "admin_otp_next"
VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"


def _captcha_ok(request) -> bool:
    secreto = settings.RECAPTCHA_SECRET_KEY
    if not secreto:
        return True
    token = request.POST.get("g-recaptcha-response", "")
    if not token:
        return False
    try:
        r = requests.post(VERIFY_URL, data={
            "secret": secreto, "response": token,
            "remoteip": request.META.get("REMOTE_ADDR", ""),
        }, timeout=10)
        datos = r.json()
        if not datos.get("success"):
            return False
        puntaje = datos.get("score")
        if puntaje is not None and puntaje < CAPTCHA_PUNTAJE_MIN:
            return False
        accion = datos.get("action")
        return accion is None or accion == CAPTCHA_ACCION
    except requests.RequestException:
        # Google caído no puede dejarte fuera de tu propio panel para siempre,
        # pero tampoco se abre la puerta: se niega este intento y se registra.
        logger.exception("No se pudo verificar reCAPTCHA")
        return False


def _emitir_codigo(user: User) -> str:
    codigo = f"{secrets.randbelow(10**6):06d}"
    OneTimeToken.objects.filter(
        user=user, purpose=TokenPurpose.ADMIN_OTP, used_at__isnull=True,
    ).update(used_at=timezone.now())
    OneTimeToken.objects.create(
        user=user, purpose=TokenPurpose.ADMIN_OTP,
        # El código va al correo; el sufijo aleatorio conserva la unicidad
        # global del campo token sin que un código corto pueda chocar con el
        # de otro usuario.
        token=f"{codigo}.{secrets.token_urlsafe(16)}",
        expires_at=timezone.now() + timedelta(minutes=OTP_MINUTOS),
    )
    return codigo


def _codigo_valido(user: User, codigo: str) -> bool:
    token = (
        OneTimeToken.objects.usable()
        .filter(user=user, purpose=TokenPurpose.ADMIN_OTP).first()
    )
    if token is None or not codigo:
        return False
    if hmac.compare_digest(token.token.split(".")[0], codigo):
        token.consume()
        return True
    return False


def _ofuscado(correo: str) -> str:
    usuario, _, dominio = correo.partition("@")
    return f"{usuario[:2]}***@{dominio}"


def _limpiar(request) -> None:
    for clave in (SESION_USER, SESION_INTENTOS, SESION_NEXT):
        request.session.pop(clave, None)


def _paso_credenciales(request, contexto):
    if not _captcha_ok(request):
        contexto["error"] = "No pudimos verificar que seas una persona. Recarga la página e inténtalo de nuevo."
        return render(request, "admin_guard/login.html", contexto)
    user = authenticate(
        request,
        username=(request.POST.get("username") or "").strip(),
        password=request.POST.get("password") or "",
    )
    if user is None or not user.is_staff or not user.is_active:
        # Mismo mensaje exista o no la cuenta: el login no confirma correos.
        contexto["error"] = "Credenciales inválidas."
        return render(request, "admin_guard/login.html", contexto)
    codigo = _emitir_codigo(user)
    send_email(
        subject="Tu código de acceso al admin",
        to=user.email,
        template="admin_otp",
        context={"codigo": codigo, "minutos": OTP_MINUTOS},
        text=(
            f"Tu código de acceso al panel de administración es {codigo}. "
            f"Vence en {OTP_MINUTOS} minutos. Si no fuiste tú, cambia tu "
            "contraseña de inmediato."
        ),
    )
    request.session[SESION_USER] = str(user.pk)
    request.session[SESION_INTENTOS] = 0
    request.session[SESION_NEXT] = request.POST.get("next") or request.GET.get("next") or ""
    return render(request, "admin_guard/codigo.html", {"correo": _ofuscado(user.email)})


def _paso_codigo(request, contexto):
    user_id = request.session.get(SESION_USER)
    if not user_id:
        return redirect(request.path)
    intentos = request.session.get(SESION_INTENTOS, 0) + 1
    request.session[SESION_INTENTOS] = intentos
    user = User.objects.filter(pk=user_id, is_staff=True, is_active=True).first()
    if user and intentos <= MAX_INTENTOS and _codigo_valido(
        user, (request.POST.get("codigo") or "").strip(),
    ):
        siguiente = request.session.get(SESION_NEXT) or ""
        _limpiar(request)
        auth_login(request, user)
        seguro = url_has_allowed_host_and_scheme(
            siguiente, allowed_hosts={request.get_host()},
        )
        return redirect(siguiente if seguro else "/admin/")
    if not user or intentos >= MAX_INTENTOS:
        _limpiar(request)
        contexto["error"] = "Demasiados intentos. Vuelve a empezar."
        return render(request, "admin_guard/login.html", contexto)
    return render(request, "admin_guard/codigo.html", {
        "correo": _ofuscado(user.email),
        "error": "Código incorrecto o vencido.",
    })


@never_cache
def admin_login(request):
    if request.user.is_authenticated and request.user.is_staff:
        return redirect("/admin/")
    contexto = {"site_key": settings.RECAPTCHA_SITE_KEY,
                "next": request.GET.get("next") or ""}
    if request.method == "POST" and request.POST.get("paso") == "credenciales":
        return _paso_credenciales(request, contexto)
    if request.method == "POST" and request.POST.get("paso") == "codigo":
        return _paso_codigo(request, contexto)
    # GET: siempre arranca de cero; un paso 2 a medias no debe quedar colgado.
    _limpiar(request)
    return render(request, "admin_guard/login.html", contexto)
