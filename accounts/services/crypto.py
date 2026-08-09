"""Cifrado simétrico para los secretos que sí hay que poder recuperar.

Una contraseña de usuario se guarda con hash porque solo hay que *comparar*
contra ella. La clave SOL es distinta: hay que enviársela a SUNAT cada vez que
el worker sincroniza, así que tiene que poder descifrarse. Se cifra con Fernet
(AES-128-CBC + HMAC) usando una llave que vive fuera de la base de datos, en
``FIELD_ENCRYPTION_KEY``.

Consecuencia práctica, escrita aquí para que nadie la descubra tarde: quien
tenga la base **y** la llave puede leer las claves SOL. Sepáralas — la llave en
el gestor de secretos del entorno, nunca en el repositorio ni en el mismo
backup que la base. Si la llave se pierde, las credenciales guardadas dejan de
poder descifrarse y cada empresa tendrá que volver a conectarse; eso es una
molestia, no una brecha.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


class DecryptionFailed(Exception):
    """El texto cifrado no corresponde a la llave activa."""


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = getattr(settings, "FIELD_ENCRYPTION_KEY", "") or ""
    if not key:
        raise ImproperlyConfigured(
            "Falta FIELD_ENCRYPTION_KEY. Genera una con "
            "`python -c \"from cryptography.fernet import Fernet; "
            'print(Fernet.generate_key().decode())"` y ponla en el entorno.'
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEY no es una llave Fernet válida (32 bytes en "
            "base64 url-safe)."
        ) from exc


def encrypt(value: str) -> str:
    """Texto plano → texto cifrado en base64. Una cadena vacía queda vacía."""
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        # Nunca se registra el valor: solo que falló, para no filtrarlo al log.
        logger.error("No se pudo descifrar un secreto con la llave activa")
        raise DecryptionFailed(
            "El secreto guardado no se puede descifrar con la llave actual."
        ) from exc
