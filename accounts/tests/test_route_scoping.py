"""Ninguna ruta de datos puede servir sin saber de qué empresa es.

``accounts/tenancy.py`` dice que existe un test que recorre las rutas y detecta
las vistas que sirven datos sin dueño. No existía, y por eso
``/api/compliance/summary/`` estuvo devolviendo la calificación más reciente de
*cualquier* contribuyente: una empresa recién registrada veía el cumplimiento
de otra.

El test de ``test_isolation`` comprueba comportamiento, pero recorriendo una
lista de URLs escrita a mano — y una ruta que nadie añade a esa lista no la
vigila nadie. Este comprueba estructura: recorre el URLconf entero, así que una
vista nueva queda cubierta el día que se enruta, sin que haya que acordarse.

Si este test falla, la vista nueva debe heredar de ``OrganizationAPIView`` (o
usar ``TenantScopedViewSetMixin``). Añadirla a ``PERMITIDAS`` es la excepción,
no el arreglo, y solo vale si de verdad no sirve datos de una empresa.
"""

from __future__ import annotations

from django.test import SimpleTestCase
from django.urls import get_resolver
from django.urls.resolvers import URLPattern, URLResolver

from accounts.tenancy import HasOrganization

# Endpoints de autenticación: se llaman antes de tener empresa, o resuelven
# qué empresas tiene quien llama. Por definición no pueden acotarse por una.
PREFIJOS_PERMITIDOS = ("api/auth/",)

RUTAS_PERMITIDAS = {
    "api/me/",             # el perfil de la persona, no de la empresa
    "api/organizations/",  # justamente, la lista de sus empresas
    "api/calendario/",     # vencimientos calculados del dígito del RUC; no lee la base
    # Suscripción de calendario por token. Es la excepción deliberada: Google
    # Calendar y Apple Calendar no mandan cabeceras de autenticación, así que
    # la empresa se identifica por un token secreto en la URL en lugar de por
    # la membresía. Sirve solo el .ics del cronograma —derivado del dígito del
    # RUC—, nunca alertas ni buzón. Ver sensor_sunat/views_app.py.
    "api/calendario/suscripcion/<str:token>.ics",
}

# Índices del router navegable de DRF: listan URLs, no datos.
CLASES_PERMITIDAS = {"APIRootView"}


def recorrer(patterns, prefijo=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            yield from recorrer(p.url_patterns, prefijo + str(p.pattern))
        elif isinstance(p, URLPattern):
            yield prefijo + str(p.pattern), p.callback


def acotada(cls) -> bool:
    """True si la vista exige resolver la empresa antes de responder.

    Se mira el permiso y no la clase base: ``TenantScopedViewSetMixin`` y las
    vistas que declaran ``permission_classes`` a mano llegan por caminos
    distintos al mismo sitio.
    """
    permisos = list(getattr(cls, "permission_classes", []) or [])
    return any(
        isinstance(p, type) and issubclass(p, HasOrganization) for p in permisos
    )


class RutasAcotadasPorEmpresaTests(SimpleTestCase):
    def test_toda_ruta_de_datos_resuelve_la_empresa(self):
        sin_acotar = []

        for ruta, callback in recorrer(get_resolver().url_patterns):
            if not ruta.startswith("api/"):
                continue
            if ruta.startswith(PREFIJOS_PERMITIDOS) or ruta in RUTAS_PERMITIDAS:
                continue
            cls = getattr(callback, "cls", None) or getattr(
                callback, "view_class", None
            )
            if cls is not None and cls.__name__ in CLASES_PERMITIDAS:
                continue
            if cls is None or not acotada(cls):
                nombre = cls.__name__ if cls else f"<función {callback.__name__}>"
                sin_acotar.append(f"{ruta} → {nombre}")

        self.assertEqual(
            sin_acotar, [],
            "Estas rutas sirven datos sin acotar a la empresa de quien llama:\n  "
            + "\n  ".join(sorted(set(sin_acotar))),
        )
