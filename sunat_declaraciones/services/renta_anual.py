"""La DJ Anual de Renta desde la plataforma ``e-renta`` de SUNAT.

No entra por el menú SOL: la app «Declaración y Pago» tiene su propio cliente
OAuth (``03590141-…``) y se llega por el *loader* público
``https://e-renta.sunat.gob.pe/loader/recaudaciontributaria/declaracionpago/formularios``,
que redirige al login de SUNAT —el mismo formulario de RUC/usuario/clave— y
vuelve con un Bearer. Con ese Bearer (y las cabeceras ``x-custom-ticket`` y
``version-web`` que la app añade) la API responde:

* ``parametriaformulario/web/formulario`` — qué formularios y ejercicios hay.
* ``consultadeclaracion/e/presentacion/resumen?numRuc&numEjercicio&formulario``
  — las presentaciones de un ejercicio (id, nro. de orden, fecha, pago).
* ``…/presentacion/detallado/{id}`` — el formulario entero: ``listCasillas``.
* ``visorconstancia/completo/{id}`` — el zip del botón «Descargar».

Verificado 2026-08-28 con una empresa real. La ruta ``/formularios/consultas``
no se puede cargar directa (500): es una ruta de la SPA y solo la API importa.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from ..models import DeclaracionAnual

logger = logging.getLogger(__name__)

LOADER_URL = "https://e-renta.sunat.gob.pe/loader/recaudaciontributaria/declaracionpago/formularios?idFormulario=menu"
API = "https://e-renta.sunat.gob.pe/v1/recaudacion/declaracionespago/renta"
FORMULARIO_EMPRESAS = "0710"
DEFAULT_TIMEOUT_MS = 90_000
POLL_MS = 500
REGLA_ANUAL = "tax-annual-income"

# Casillas del 710 con nombre, para que nadie tenga que saberse los números.
CASILLAS = {
    # Balance
    "359": "efectivo", "361": "cuentas_por_cobrar_comerciales", "365": "cuentas_por_cobrar_relacionadas",
    "368": "mercaderias", "382": "propiedad_planta_equipo", "383": "depreciacion_acumulada",
    "390": "total_activo",
    "402": "tributos_por_pagar", "403": "remuneraciones_por_pagar", "404": "cuentas_por_pagar_comerciales",
    "409": "obligaciones_financieras", "410": "provisiones", "412": "total_pasivo",
    "414": "capital", "421": "resultados_acumulados", "422": "resultados_acumulados_negativos",
    "423": "utilidad_del_ejercicio", "424": "perdida_del_ejercicio", "425": "total_patrimonio",
    "426": "total_pasivo_patrimonio",
    # Estado de resultados
    "461": "ventas_brutas", "463": "ventas_netas", "464": "costo_de_ventas",
    "466": "utilidad_bruta", "467": "perdida_bruta", "468": "gastos_de_ventas",
    "469": "gastos_de_administracion", "470": "utilidad_operativa", "471": "perdida_operativa",
    "472": "gastos_financieros", "473": "ingresos_financieros", "475": "otros_ingresos",
    "484": "utilidad_antes_de_participaciones", "485": "perdida_antes_de_participaciones",
    "487": "utilidad_antes_de_impuesto", "489": "perdida_antes_de_impuesto",
    "490": "impuesto_a_la_renta_gasto", "492": "utilidad_neta", "493": "perdida_neta",
    # Impuesto y deuda
    "100": "utilidad_tributaria", "101": "perdida_tributaria", "103": "adiciones", "105": "deducciones",
    "106": "renta_neta", "107": "perdida_neta_tributaria", "108": "perdidas_compensables",
    "110": "renta_neta_imponible", "113": "impuesto_a_la_renta", "111": "saldo_perdidas_no_compensadas",
    "127": "saldo_a_favor_anterior", "128": "pagos_a_cuenta", "130": "retenciones",
    "131": "pagos_itan", "138": "saldo_a_favor", "139": "saldo_a_favor_del_fisco",
    "144": "pagos_previos", "145": "interes_moratorio", "180": "importe_a_pagar",
    "610": "coeficiente_pago_a_cuenta",
}
# Casillas que el formulario muestra en negativo (entre paréntesis): pérdidas
# y gastos. Se guardan como positivos; el nombre ya dice qué son.
SIGNO_LIBRE = {"506"}


class RentaAnualError(RuntimeError):
    """e-renta entró pero no se pudo leer."""


class RentaAnualLoginRejected(RentaAnualError):
    """SUNAT rechazó usuario o clave en el login de e-renta."""


@dataclass
class PresentacionAnual:
    ejercicio: str
    formulario: str
    resumen: dict[str, Any]
    detallado: dict[str, Any] | None = None
    zip_bytes: bytes | None = None


@dataclass
class RentaAnualClient:
    ruc: str
    username: str
    password: str
    headless: bool = True
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    _headers: dict[str, str] = field(default_factory=dict, init=False)

    def consultar(
        self, *, formulario: str = FORMULARIO_EMPRESAS, omitir: set[str] | None = None,
        ejercicios: list[str] | None = None,
    ) -> list[PresentacionAnual]:
        """Todas las presentaciones del formulario, con detalle y zip salvo
        las que ya se tienen (``omitir`` = números de orden)."""
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        from core.browser import browser_env
        from sunat_mailbox.services.constants import BROWSER_ARGS, USER_AGENT

        def sniff(request: Any) -> None:
            auth = request.headers.get("authorization", "")
            if "e-renta" in request.url and "/v1/" in request.url and auth.startswith("Bearer "):
                self._headers = {
                    "authorization": auth,
                    "x-custom-ticket": request.headers.get("x-custom-ticket", ""),
                    "version-web": request.headers.get("version-web", ""),
                    "Accept": "application/json, text/plain, */*",
                }

        salida: list[PresentacionAnual] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=self.headless, channel="chromium", args=BROWSER_ARGS, env=browser_env(),
            )
            try:
                ctx = browser.new_context(user_agent=USER_AGENT, locale="es-PE", viewport={"width": 1440, "height": 1000})
                page = ctx.new_page()
                page.on("dialog", lambda d: d.accept())
                page.on("request", sniff)
                page.goto(LOADER_URL, wait_until="networkidle", timeout=self.timeout_ms)
                self._login(page)
                self._esperar_bearer(page)

                ejercicios = ejercicios or self._ejercicios(ctx, formulario)
                for ejercicio in ejercicios:
                    resumen = self._json(ctx, f"{API}/consultadeclaracion/e/presentacion/resumen"
                                              f"?numRuc={self.ruc}&numEjercicio={ejercicio}&formulario={formulario.lstrip('0')}&indMedPres=1")
                    for pres in (resumen or {}).get("presentacion") or []:
                        item = PresentacionAnual(ejercicio=ejercicio, formulario=formulario, resumen=pres)
                        if str(pres.get("numOrden")) in (omitir or set()):
                            salida.append(item)
                            continue
                        pid = pres.get("idPresentacion")
                        if pid:
                            item.detallado = self._json(ctx, f"{API}/consultadeclaracion/e/presentacion/detallado/{pid}")
                            item.zip_bytes = self._bytes(ctx, f"{API}/visorconstancia/completo/{pid}")
                        salida.append(item)
                logger.info("Renta anual %s: %d presentaciones en %d ejercicios", self.ruc, len(salida), len(ejercicios))
            except PlaywrightError as exc:
                raise RentaAnualError(f"El navegador falló en e-renta: {exc}") from exc
            finally:
                browser.close()
        return salida

    def _login(self, page: Any) -> None:
        from ruc_profile.services.sol_ficha import SolLoginRejected, _mensaje_de_login

        page.wait_for_selector("#txtRuc", state="visible", timeout=self.timeout_ms)
        page.click("#btnPorRuc")
        page.fill("#txtRuc", self.ruc)
        page.fill("#txtUsuario", self.username)
        page.fill("#txtContrasena", self.password)
        page.click("#btnAceptar")
        try:
            page.wait_for_url(lambda u: "e-renta.sunat.gob.pe/app" in u, timeout=self.timeout_ms)
        except Exception as exc:  # noqa: BLE001
            raise RentaAnualLoginRejected(
                _mensaje_de_login(page) or "SUNAT no aceptó el usuario o la clave SOL en e-renta."
            ) from exc
        del SolLoginRejected  # solo se importa por simetría con la ficha

    def _esperar_bearer(self, page: Any) -> None:
        for _ in range(self.timeout_ms // POLL_MS):
            if self._headers:
                return
            page.wait_for_timeout(POLL_MS)
        raise RentaAnualError("e-renta abrió pero no llamó a su API: no se capturó el token.")

    def _ejercicios(self, ctx: Any, formulario: str) -> list[str]:
        datos = self._json(ctx, f"{API}/parametriaformulario/web/formulario") or []
        for f in datos:
            if str(f.get("codFormulario")) == formulario:
                return [str(e["ejercicio"]) for e in f.get("ejercicios") or [] if e.get("ejercicio")]
        return []

    def _json(self, ctx: Any, url: str) -> Any:
        r = ctx.request.get(url, headers=self._headers, timeout=60_000)
        if r.status != 200:
            raise RentaAnualError(f"e-renta respondió {r.status} en {url}")
        return r.json()

    def _bytes(self, ctx: Any, url: str) -> bytes | None:
        try:
            r = ctx.request.get(url, headers=self._headers, timeout=120_000)
            if r.status != 200:
                logger.warning("e-renta respondió %s al descargar %s", r.status, url)
                return None
            cuerpo = r.body()
            return cuerpo if cuerpo.startswith(b"PK") else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo descargar %s: %s", url, exc)
            return None


# ----------------------------------------------------------------- parseo

def _decimal(valor: Any) -> Decimal | None:
    try:
        return Decimal(str(valor).strip().replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def casillas_de(detallado: dict[str, Any] | None) -> dict[str, str]:
    """``{"461": "387310", ...}`` desde ``listFormularios[0].listCasillas``."""
    if not detallado:
        return {}
    formularios = detallado.get("listFormularios") or []
    if not formularios:
        return {}
    return {
        str(c.get("numCas")): str(c.get("desValCas") if c.get("desValCas") is not None else "").strip()
        for c in formularios[0].get("listCasillas") or []
        if c.get("numCas") is not None and c.get("indDel", "0") != "1"
    }


def tributos_de(detallado: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not detallado:
        return []
    formularios = detallado.get("listFormularios") or []
    if not formularios:
        return []
    return [
        {
            "codigo": str(t.get("codTri") or "").strip(),
            "deuda": t.get("mtoTotDeu"),
            "pagado": t.get("mtoPagTot"),
            "base": t.get("mtoBasImp"),
        }
        for t in formularios[0].get("listTributosPagados") or []
    ]


def con_nombre(casillas: dict[str, str]) -> dict[str, Decimal | None]:
    """Las casillas que importan, por nombre."""
    return {nombre: _decimal(casillas.get(num)) for num, nombre in CASILLAS.items()}


def anexos_de(zip_bytes: bytes | None) -> dict[str, list[list[Any]]]:
    """Las tablas de los Excel del zip (socios, pagos previos, alquileres,
    donaciones, deudas). Los archivos vienen con extensión ``.xls`` pero son
    OOXML; openpyxl los lee desde memoria sin mirar la extensión."""
    if not zip_bytes:
        return {}
    import openpyxl

    salida: dict[str, list[list[Any]]] = {}
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        return {}
    for nombre in zf.namelist():
        if not nombre.lower().endswith((".xls", ".xlsx")):
            continue
        clave = re.sub(r"^F\d+_\d+_", "", nombre.rsplit(".", 1)[0])
        try:
            libro = openpyxl.load_workbook(io.BytesIO(zf.read(nombre)), read_only=True, data_only=True)
            filas = [
                [c if not isinstance(c, datetime) else c.isoformat() for c in fila]
                for fila in libro.worksheets[0].iter_rows(values_only=True)
                if any(c is not None for c in fila)
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo leer el anexo %s: %s", nombre, exc)
            continue
        salida[clave] = filas
    return salida


def _fecha(texto: Any) -> datetime | None:
    if not texto:
        return None
    for formato in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return timezone.make_aware(datetime.strptime(str(texto), formato))
        except ValueError:
            continue
    return None


# --------------------------------------------------------------- persistir

@dataclass
class ResultadoRentaAnual:
    presentaciones: int = 0
    nuevas: int = 0
    actualizadas: int = 0
    evidencias: int = 0


@transaction.atomic
def guardar(account_ruc: str, presentaciones: list[PresentacionAnual]) -> ResultadoRentaAnual:
    hoy = timezone.localdate()
    resultado = ResultadoRentaAnual(presentaciones=len(presentaciones))
    for p in presentaciones:
        r = p.resumen
        nro_orden = str(r.get("numOrden") or "")
        if not nro_orden:
            continue
        actual = DeclaracionAnual.objects.filter(account_ruc=account_ruc, nro_orden=nro_orden).first()
        datos = {
            "ejercicio": p.ejercicio,
            "formulario": p.formulario,
            "id_presentacion": str(r.get("idPresentacion") or ""),
            "tipo_declaracion": str(r.get("desTipoDeclaracion") or ""),
            "rectificatoria": str(r.get("tipoDeclaracion") or "0") not in ("", "0"),
            "fecha_presentacion": _fecha(r.get("fecDeclaracion")),
            "medio_pago": str(r.get("desMedPago") or ""),
            "importe_pagado": Decimal(str(r.get("mtoPag") or 0)),
            "raw_resumen": r,
            "visto_el": hoy,
        }
        if p.detallado is not None:
            datos.update({
                "casillas": casillas_de(p.detallado),
                "tributos": tributos_de(p.detallado),
                "raw_detallado": {k: v for k, v in p.detallado.items() if k != "preDeclaracion"},
            })
        if p.zip_bytes:
            datos["anexos"] = anexos_de(p.zip_bytes)
        if actual is None:
            actual = DeclaracionAnual(account_ruc=account_ruc, nro_orden=nro_orden, **datos)
            if p.zip_bytes:
                actual.archivo.save(f"{account_ruc}_F{p.formulario}_{p.ejercicio}_{nro_orden}.zip", ContentFile(p.zip_bytes), save=False)
            actual.save()
            resultado.nuevas += 1
        else:
            for k, v in datos.items():
                setattr(actual, k, v)
            if p.zip_bytes and not actual.archivo:
                actual.archivo.save(f"{account_ruc}_F{p.formulario}_{p.ejercicio}_{nro_orden}.zip", ContentFile(p.zip_bytes), save=False)
            actual.save()
            resultado.actualizadas += 1
    resultado.evidencias = registrar_evidencia(account_ruc)
    # Depreciación y ajuste del impuesto van al Estado de Resultados.
    from financials.services.ingest import ingest_annual

    ingest_annual(account_ruc)
    try:
        from finance_analytics import cache as overview_cache

        overview_cache.invalidate(account_ruc)
    except Exception:  # noqa: BLE001
        logger.debug("No se pudo invalidar la caché del overview", exc_info=True)
    return resultado


def vigentes(account_ruc: str, formulario: str = FORMULARIO_EMPRESAS) -> dict[str, DeclaracionAnual]:
    """Ejercicio → la última presentación (la rectificatoria más reciente manda)."""
    out: dict[str, DeclaracionAnual] = {}
    for d in DeclaracionAnual.objects.filter(account_ruc=account_ruc, formulario=formulario).order_by("ejercicio", "fecha_presentacion"):
        out[d.ejercicio] = d
    return out


def registrar_evidencia(account_ruc: str) -> int:
    """Cada DJ anual presentada es evidencia verificada de la obligación anual,
    vigente hasta que toque la del ejercicio siguiente (fin de abril)."""
    from datetime import date

    from obligations import enums
    from obligations.models import CompanyObligation, ObligationEvidence

    obligacion = CompanyObligation.objects.filter(account_ruc=account_ruc, rule__code=REGLA_ANUAL).first()
    if obligacion is None:
        return 0
    n = 0
    for ejercicio, decl in vigentes(account_ruc).items():
        ref = {"model": "sunat_declaraciones.DeclaracionAnual", "id": str(decl.pk), "period": f"{ejercicio}13", "nro_orden": decl.nro_orden}
        ObligationEvidence.objects.update_or_create(
            company_obligation=obligacion, reference__period=ref["period"], reference__model=ref["model"],
            defaults={
                "evidence_type": enums.EvidenceType.DECLARATION,
                "verification_status": enums.VerificationStatus.VERIFIED,
                "label": f"F.V. {decl.formulario} · ejercicio {ejercicio} · orden {decl.nro_orden}",
                "reference": ref,
                "valid_from": decl.fecha_presentacion.date() if decl.fecha_presentacion else None,
                "valid_until": date(int(ejercicio) + 2, 4, 30),
                "notes": "Presentación registrada en SUNAT (e-renta, consulta de declaraciones).",
                "verified_at": timezone.now(),
            },
        )
        n += 1
    return n


def sincronizar_renta_anual(account_ruc: str, username: str, password: str, *, headless: bool = True) -> ResultadoRentaAnual:
    ya = set(
        DeclaracionAnual.objects.filter(account_ruc=account_ruc).exclude(casillas={}).values_list("nro_orden", flat=True)
    )
    cliente = RentaAnualClient(account_ruc, username, password, headless=headless)
    presentaciones = cliente.consultar(omitir=ya)
    resultado = guardar(account_ruc, presentaciones)
    logger.info("Renta anual %s: %d presentaciones (%d nuevas, %d actualizadas)", account_ruc, resultado.presentaciones, resultado.nuevas, resultado.actualizadas)
    return resultado


# ------------------------------------------------------------------ lectura

def _num(v: Decimal | None) -> float | None:
    return None if v is None else float(v)


def resumen_anual(account_ruc: str) -> list[dict[str, Any]]:
    """Una entrada por ejercicio con lo que un dueño quiere ver de su DJ anual."""
    salida = []
    for ejercicio, d in sorted(vigentes(account_ruc).items(), reverse=True):
        c = con_nombre(d.casillas)
        salida.append({
            "ejercicio": ejercicio,
            "formulario": d.formulario,
            "nro_orden": d.nro_orden,
            "fecha_presentacion": d.fecha_presentacion,
            "tipo_declaracion": d.tipo_declaracion,
            "rectificatoria": d.rectificatoria,
            "medio_pago": d.medio_pago,
            "importe_pagado": _num(d.importe_pagado),
            "resultados": {k: _num(c[k]) for k in (
                "ventas_netas", "costo_de_ventas", "utilidad_bruta", "perdida_bruta",
                "gastos_de_administracion", "gastos_de_ventas", "utilidad_operativa", "perdida_operativa",
                "gastos_financieros", "utilidad_neta", "perdida_neta",
            )},
            "balance": {k: _num(c[k]) for k in (
                "efectivo", "cuentas_por_cobrar_comerciales", "cuentas_por_cobrar_relacionadas",
                "propiedad_planta_equipo", "total_activo", "tributos_por_pagar", "remuneraciones_por_pagar",
                "cuentas_por_pagar_comerciales", "obligaciones_financieras", "total_pasivo",
                "capital", "resultados_acumulados", "total_patrimonio",
            )},
            "impuesto": {k: _num(c[k]) for k in (
                "renta_neta_imponible", "perdida_neta_tributaria", "impuesto_a_la_renta",
                "pagos_a_cuenta", "saldo_a_favor", "saldo_a_favor_del_fisco", "importe_a_pagar",
                "coeficiente_pago_a_cuenta", "saldo_perdidas_no_compensadas",
            )},
            "anexos": {k: len(v) - 1 for k, v in (d.anexos or {}).items()},
            "tiene_archivo": bool(d.archivo),
        })
    return salida


__all__ = [
    "CASILLAS", "DeclaracionAnual", "PresentacionAnual", "RentaAnualClient", "RentaAnualError",
    "RentaAnualLoginRejected", "ResultadoRentaAnual", "anexos_de", "casillas_de", "con_nombre",
    "guardar", "registrar_evidencia", "resumen_anual", "sincronizar_renta_anual", "tributos_de",
    "vigentes",
]
