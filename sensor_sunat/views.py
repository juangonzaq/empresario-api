"""API pública del generador de calendario tributario (lead magnet, sin login)."""

from datetime import date

from django.core.cache import cache
from django.http import HttpResponse, JsonResponse

from .calendario import DATA, a_ics, eventos_para, grupo_de, serializar, validar_ruc

THROTTLE_MAX = 20        # requests por IP
THROTTLE_VENTANA = 3600  # segundos


def _ip_cliente(request) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    return xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR", "")


def _autenticado(request) -> bool:
    """¿Viene de alguien con sesión en la aplicación?

    La vista nació como lead magnet abierto, y el tope por IP está para que
    nadie la use de scraper. Ahora también la consume el calendario de la
    aplicación, donde el mismo usuario cambia de régimen o marca la planilla
    varias veces seguidas: con veinte peticiones por hora y una oficina detrás
    de una sola IP, la pantalla se quedaría sin calendario a media tarde. Se
    valida el token de verdad —no basta con que venga la cabecera— porque si
    no, saltarse el límite sería tan fácil como inventarse una.
    """
    from rest_framework.exceptions import AuthenticationFailed
    from rest_framework_simplejwt.authentication import JWTAuthentication

    try:
        return JWTAuthentication().authenticate(request) is not None
    except AuthenticationFailed:
        return False


def _throttled(request) -> bool:
    if _autenticado(request):
        return False
    clave = f"calendario_throttle:{_ip_cliente(request)}"
    actual = cache.get(clave, 0)
    if actual >= THROTTLE_MAX:
        return True
    if actual == 0:
        cache.set(clave, 1, THROTTLE_VENTANA)
    else:
        try:
            cache.incr(clave)
        except ValueError:  # la clave expiró entre get e incr
            cache.set(clave, 1, THROTTLE_VENTANA)
    return False


def api_calendario(request):
    ruc = request.GET.get("ruc", "").strip()
    if not validar_ruc(ruc):
        return JsonResponse({"error": "RUC inválido"}, status=400)
    if _throttled(request):
        return JsonResponse({"error": "Demasiadas solicitudes"}, status=429)

    regimen = request.GET.get("regimen", "RMT").upper()
    if regimen not in ("RUS", "RER", "RMT", "RG"):
        return JsonResponse({"error": "Régimen inválido (RUS | RER | RMT | RG)"}, status=400)

    desde = None
    if request.GET.get("desde"):
        try:
            desde = date.fromisoformat(request.GET["desde"])
        except ValueError:
            return JsonResponse({"error": "Parámetro 'desde' inválido (YYYY-MM-DD)"}, status=400)

    ev = eventos_para(
        ruc,
        planilla=request.GET.get("planilla", "1") == "1",
        bc=request.GET.get("bc", "0") == "1",
        regimen=regimen,
        desde=desde,
    )

    if request.GET.get("formato") == "ics":
        resp = HttpResponse(a_ics(ruc, ev), content_type="text/calendar; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="calendario_sunat_{ruc}.ics"'
        return resp

    return JsonResponse({
        "ruc": ruc,
        "grupo": grupo_de(ruc),
        "anio": DATA["anio"],
        "eventos": [serializar(e) for e in ev],
    })
