"""El desafío del CAPTCHA, sostenido entre dos peticiones HTTP.

Pedir la imagen y responderla son dos llamadas distintas, y entre medias hay
una persona mirando la pantalla. Algo tiene que recordar los tokens y las
cookies de esa visita a AFPnet mientras tanto.

Va a la caché compartida y no a la base de datos porque es basura a los cinco
minutos: un desafío sin responder no merece una fila ni un borrado programado.
La caché ya es Redis y está compartida entre workers, que es lo que hace falta
para que el paso 2 lo atienda un proceso distinto del que atendió el paso 1.

Lo que se guarda **no incluye secretos de la empresa**: son las cookies
anónimas de una visita y los tokens del formulario. La clave viaja en la
petición del paso 2, se usa y se descarta.
"""

from __future__ import annotations

import secrets

from django.core.cache import cache

from . import client

# Un CAPTCHA que lleva cinco minutos en pantalla ya casi seguro caducó en el
# portal. Expirar antes aquí convierte un error confuso de AFPnet en un mensaje
# nuestro que se entiende.
TTL_SEGUNDOS = 300

PREFIJO = "afpnet_desafio"


def _clave(organization_id, handle: str) -> str:
    # La empresa entra en la clave: un handle robado no sirve desde otra
    # sesión, porque la vista lo busca bajo la empresa de quien llama.
    return f"{PREFIJO}:{organization_id}:{handle}"


def crear(organization) -> tuple[str, str]:
    """Pide un CAPTCHA a AFPnet y lo guarda a la espera de respuesta.

    Devuelve ``(handle, imagen)``: el primero identifica el desafío, el segundo
    es el ``data:`` URI que la pantalla pinta directamente.
    """
    desafio = client.pedir_desafio()
    handle = secrets.token_urlsafe(24)
    cache.set(_clave(organization.id, handle), desafio.estado, TTL_SEGUNDOS)
    return handle, desafio.captcha_data_uri


def resolver(organization, handle: str, usuario: str, clave: str, captcha: str):
    """Completa el login y deja la sesión guardada.

    Devuelve la ``AfpnetSession`` ya activa. Propaga ``LoginRechazado`` para que
    la vista distinga un CAPTCHA mal escrito —que se reintenta— de unas
    credenciales malas, que **no** se reintentan.
    """
    from afpnet.models import AfpnetSession

    entrada = _clave(organization.id, handle)
    estado = cache.get(entrada)
    if estado is None:
        raise client.DesafioCaducado(
            "La imagen caducó. Pide una nueva y vuelve a intentarlo."
        )
    # Se consume aunque el intento falle: cada imagen vale para un solo envío,
    # igual que en el portal.
    cache.delete(entrada)

    cookies = client.responder_desafio(estado, organization.ruc, usuario, clave, captcha)

    sesion, _ = AfpnetSession.objects.get_or_create(
        organization=organization, defaults={"taxpayer_id": organization.ruc},
    )
    sesion.taxpayer_id = organization.ruc
    sesion.marcar_activa(cookies, usuario)
    return sesion
