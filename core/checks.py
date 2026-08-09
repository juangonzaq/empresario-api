"""Comprobaciones que impiden arrancar mal configurado.

Django ya trae ``manage.py check --deploy``, pero eso hay que acordarse de
correrlo. Estas se ejecutan siempre, en cada arranque y en cada comando, y con
``DEBUG=False`` son errores: el proceso no levanta.

Lo que se vigila es lo que convierte un despliegue descuidado en una brecha:
firmar los tokens con una clave que está en el repositorio, o quedarse sin la
llave con la que se descifran las claves SOL de los clientes.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, Warning, register

INSECURE_PREFIX = "django-insecure-"


def _es_produccion() -> bool:
    """Ni en desarrollo ni corriendo la batería de tests."""
    return not settings.DEBUG and not getattr(settings, "RUNNING_TESTS", False)


def _problem(msg: str, hint: str, code: str):
    """Fuera de producción avisa; en producción impide arrancar."""
    cls = Error if _es_produccion() else Warning
    return cls(msg, hint=hint, id=code)


@register()
def check_secretos(app_configs, **kwargs):
    problemas = []

    if str(settings.SECRET_KEY).startswith(INSECURE_PREFIX):
        problemas.append(_problem(
            "SECRET_KEY es el valor de ejemplo que viene en el repositorio.",
            "Define DJANGO_SECRET_KEY en el entorno. Con esta clave cualquiera "
            "que vea el código puede firmar tokens de sesión válidos para "
            "cualquier usuario.",
            "empresario.E001",
        ))

    if not getattr(settings, "FIELD_ENCRYPTION_KEY", ""):
        problemas.append(_problem(
            "Falta FIELD_ENCRYPTION_KEY: no se pueden cifrar ni leer las claves SOL.",
            'Genera una con: python -c "from cryptography.fernet import '
            'Fernet; print(Fernet.generate_key().decode())" y ponla en el '
            "entorno. Guárdala fuera del backup de la base de datos.",
            "empresario.E002",
        ))

    # Si no se separa, el JWT hereda SECRET_KEY. Funciona, pero rotar una
    # obliga a rotar la otra y cierra la sesión de todo el mundo.
    if _es_produccion() and not settings.SIMPLE_JWT.get("SIGNING_KEY"):
        problemas.append(Warning(
            "Los tokens JWT se firman con SECRET_KEY.",
            hint="Define JWT_SIGNING_KEY para poder rotar una sin la otra.",
            id="empresario.W003",
        ))

    return problemas


@register()
def check_produccion(app_configs, **kwargs):
    """Ajustes que en producción son un problema, y en desarrollo no."""
    if not _es_produccion():
        return []

    problemas = []

    if not settings.ALLOWED_HOSTS or settings.ALLOWED_HOSTS == ["localhost", "127.0.0.1"]:
        problemas.append(Error(
            "ALLOWED_HOSTS sigue con los valores de desarrollo.",
            hint="Define DJANGO_ALLOWED_HOSTS con tus dominios reales.",
            id="empresario.E004",
        ))

    localhost = [o for o in settings.CORS_ALLOWED_ORIGINS if "localhost" in o or "127.0.0.1" in o]
    if localhost:
        problemas.append(Warning(
            f"CORS acepta orígenes de desarrollo en producción: {', '.join(localhost)}.",
            hint="Define DJANGO_CORS_ALLOWED_ORIGINS solo con el dominio del frontend.",
            id="empresario.W005",
        ))

    # Sin caché compartida, los límites de peticiones se cuentan por proceso y
    # el techo real se multiplica por el número de workers.
    backend = settings.CACHES.get("default", {}).get("BACKEND", "")
    if "locmem" in backend.lower():
        problemas.append(Error(
            "La caché es local al proceso (LocMemCache).",
            hint="Configura Redis en CACHES: los límites de peticiones y el "
                 "caché del panel deben compartirse entre todos los workers.",
            id="empresario.E006",
        ))

    return problemas
