"""Límites de petición para los endpoints que un atacante ataca primero.

El límite general de DRF va por IP (anónimos) o por usuario (autenticados).
Eso no basta en dos casos:

* **Login.** Limitar por IP no frena un ataque distribuido contra una sola
  cuenta: mil IP probando cien claves cada una pasan el filtro. Por eso aquí
  se cuenta también *por cuenta atacada*.
* **Correos.** Cada solicitud de recuperación o reenvío de verificación manda
  un mensaje. Sin freno, cualquiera puede usarnos para bombardear un buzón
  ajeno y quemar la reputación de nuestro dominio.

Todos los contadores viven en la caché compartida (Redis). Con la caché en
memoria de Django cada proceso llevaría su cuenta y el límite se multiplicaría
por el número de workers.
"""

from __future__ import annotations

import hashlib

from rest_framework.permissions import SAFE_METHODS
from rest_framework.throttling import SimpleRateThrottle


class LoginPorCuentaThrottle(SimpleRateThrottle):
    """Cuenta los intentos contra un correo concreto, venga de donde venga."""

    scope = "login_por_cuenta"

    def get_cache_key(self, request, view):
        email = (request.data.get("email") or "").strip().lower()
        if not email:
            return None  # sin correo no hay nada que proteger; lo frena el de IP
        # El correo no se guarda en claro en la caché: el hash basta para
        # contar y no deja una lista de clientes en Redis.
        digest = hashlib.sha256(email.encode()).hexdigest()[:32]
        return f"throttle_login_cuenta_{digest}"


class LoginPorIpThrottle(SimpleRateThrottle):
    """Y en paralelo, cuántas cuentas distintas prueba una misma IP."""

    scope = "login_por_ip"

    def get_cache_key(self, request, view):
        return f"throttle_login_ip_{self.get_ident(request)}"


class RegistroThrottle(SimpleRateThrottle):
    scope = "registro"

    def get_cache_key(self, request, view):
        return f"throttle_registro_{self.get_ident(request)}"


class CorreoThrottle(SimpleRateThrottle):
    """Para todo lo que dispara un envío: recuperación y reenvío de verificación.

    Se cuenta por destinatario cuando se conoce, y por IP cuando no: si solo
    contáramos por IP, un atacante rotando IP podría seguir inundando el mismo
    buzón; si solo contáramos por destinatario, podría inundar muchos buzones
    distintos desde una sola máquina.
    """

    scope = "correo"

    def get_cache_key(self, request, view):
        email = (request.data.get("email") or "").strip().lower()
        if email:
            digest = hashlib.sha256(email.encode()).hexdigest()[:32]
            return f"throttle_correo_dest_{digest}"
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            return f"throttle_correo_user_{user.pk}"
        return f"throttle_correo_ip_{self.get_ident(request)}"


class SunatConexionThrottle(SimpleRateThrottle):
    """Conectar SUNAT dispara una sincronización completa: no es gratis.

    Además, repetir intentos con credenciales equivocadas puede acabar
    bloqueando el usuario SOL del cliente en SUNAT.

    Cuenta **solo lo que escribe**. Preguntar «¿cómo está mi conexión?» no
    cuesta nada ni toca SUNAT, y sin embargo gastaba cupo: el perfil y el
    onboarding hacen ese GET al abrirse, así que entrar cuatro veces a mirar
    dejaba al usuario sin poder conectar durante una hora —justo cuando venía
    a conectar—. Los límites de la casa (``UserRateThrottle``) siguen cubriendo
    la lectura.
    """

    scope = "sunat_conexion"

    def get_cache_key(self, request, view):
        # None significa «no cuentes esta petición» para DRF.
        if request.method in SAFE_METHODS:
            return None
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return f"throttle_sunat_ip_{self.get_ident(request)}"
        return f"throttle_sunat_user_{user.pk}"
