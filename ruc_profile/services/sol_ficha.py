"""Régimen tributario leído de la Ficha RUC **dentro de SOL**.

La ficha pública no publica el régimen; la de SOL sí, en «Registro de
Tributos Afectos»: una fila por tributo con su fecha de alta. De ahí sale si
la empresa está en el MYPE Tributario, el General, el RER o el RUS, sin que
nadie tenga que declararlo.

Navegación (verificada 2026-08-23): menú SOL → opción 10.1.1.1.1 «Ficha RUC»
(ol-ti-itmoddatruc/mruc001Alias) → la sección de tributos se despliega con
``opcboton('1','Tribut')``, que reenvía el formulario ``listas`` y devuelve
la página con la tabla.
"""

from __future__ import annotations

import datetime
import html as html_lib
import logging
import re
from dataclasses import dataclass
from typing import Any

from django.utils import timezone

logger = logging.getLogger(__name__)

FICHA_CODE = "10.1.1.1.1"
MENU_URL = "https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm?pestana=*&agrupacion=*"
FICHA_MARKER = "itmoddatruc"

# Texto del tributo en la ficha → régimen de renta.
PATRONES = [
    ("RMT", re.compile(r"MYPE\s*TRIBUTARIO", re.I)),
    ("RER", re.compile(r"R[ÉE]GIMEN\s*ESPECIAL|RTA\.?\s*ESP", re.I)),
    ("RUS", re.compile(r"\bRUS\b|R[ÉE]GIMEN\s*[ÚU]NICO\s*SIMPLIFICADO", re.I)),
    ("RG", re.compile(r"RENTA\s*-?\s*3RA\.?\s*CATEG\w*\.?\s*-?\s*CTA\.?\s*PROPIA|3RA\.?\s*CATEGOR", re.I)),
]


@dataclass
class Tributo:
    descripcion: str
    fecha_alta: datetime.date | None
    afecto_desde: datetime.date | None


def _fecha(s: str) -> datetime.date | None:
    s = (s or "").strip()
    try:
        return datetime.datetime.strptime(s, "%d/%m/%Y").date()
    except ValueError:
        return None


def parse_tributos(page_html: str) -> list[Tributo]:
    """Las filas de «Registro de Tributos Afectos» de la ficha ya desplegada."""
    i = page_html.find("Registro de Tributos Afectos")
    if i < 0:
        return []
    j = page_html.find("Representantes Legales", i)
    seg = page_html[i: j if j > 0 else None]
    out: list[Tributo] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", seg, re.S):
        cells = [html_lib.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        cells = [c for c in cells if c]
        if len(cells) < 3 or cells[0].lower().startswith("tributo"):
            continue
        if not re.match(r"\d{2}/\d{2}/\d{4}", cells[1]):
            continue
        out.append(Tributo(cells[0], _fecha(cells[1]), _fecha(cells[2])))
    return out


def detect_regime(tributos: list[Tributo]) -> str | None:
    """El régimen de renta entre los tributos afectos; None si no hay renta
    empresarial (p. ej., solo retenciones)."""
    for t in tributos:
        desc = t.descripcion.upper()
        if "RETENC" in desc:      # 4ta/5ta retenciones no son el régimen de la empresa
            continue
        if not desc.startswith("RENTA") and "RUS" not in desc:
            continue
        for code, rx in PATRONES:
            if rx.search(desc):
                return code
    return None


def fetch_tributos(ruc: str, username: str, password: str, *, headless: bool = True, timeout_ms: int = 90_000) -> list[Tributo]:
    """Entra a SOL, abre la Ficha RUC, despliega los tributos y los devuelve."""
    from playwright.sync_api import sync_playwright

    from core.browser import browser_env
    from sunat_mailbox.services.constants import BROWSER_ARGS, USER_AGENT

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless, channel="chromium", args=BROWSER_ARGS, env=browser_env())
        try:
            ctx = browser.new_context(user_agent=USER_AGENT, locale="es-PE", viewport={"width": 1440, "height": 1000})
            page = ctx.new_page()
            page.on("dialog", lambda d: d.accept())
            page.goto(MENU_URL, wait_until="networkidle", timeout=timeout_ms)
            page.wait_for_selector("#txtRuc", state="visible", timeout=timeout_ms)
            page.click("#btnPorRuc")
            page.fill("#txtRuc", ruc); page.fill("#txtUsuario", username); page.fill("#txtContrasena", password)
            page.click("#btnAceptar")
            page.wait_for_url(lambda u: "loginMenuSol" not in u, timeout=timeout_ms)
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            page.wait_for_timeout(2000)
            page.evaluate(
                f"ejecuta('MenuInternet.htm?action=iconExecute&code={FICHA_CODE}',false,'Ficha RUC','#nivel1_10','{FICHA_CODE}')"
            )
            frame = None
            for _ in range(timeout_ms // 500):
                frame = next((f for f in page.frames if FICHA_MARKER in f.url), None)
                if frame is not None:
                    try:
                        if "Registro de Tributos Afectos" in frame.evaluate("document.body ? document.body.innerText : ''"):
                            break
                    except Exception:  # noqa: BLE001 — el frame aún navega
                        pass
                page.wait_for_timeout(500)
            if frame is None:
                raise RuntimeError("La Ficha RUC no cargó dentro de SOL.")
            frame.evaluate("opcboton('1','Tribut')")
            page.wait_for_timeout(1500)
            frame = next((f for f in page.frames if FICHA_MARKER in f.url), frame)
            try:
                frame.wait_for_load_state("networkidle", timeout=timeout_ms)
            except Exception:  # noqa: BLE001
                pass
            content = frame.content()
        finally:
            browser.close()
    tributos = parse_tributos(content)
    logger.info("Ficha RUC SOL %s: %d tributos afectos", ruc, len(tributos))
    return tributos


def sync_regime(organization, credential) -> dict[str, Any]:
    """Lee los tributos, los guarda y fija el régimen de la empresa desde SUNAT."""
    from accounts.models import Organization
    from ruc_profile.models import RucTaxAffectation

    tributos = fetch_tributos(organization.ruc, credential.sol_username, credential.password)
    now = timezone.now()
    RucTaxAffectation.objects.filter(ruc=organization.ruc).delete()
    RucTaxAffectation.objects.bulk_create([
        RucTaxAffectation(ruc=organization.ruc, tributo=t.descripcion, fecha_alta=t.fecha_alta,
                          afecto_desde=t.afecto_desde, captured_at=now)
        for t in tributos
    ])
    regime = detect_regime(tributos)
    fields = ["tax_regime_checked_at", "updated_at"]
    organization.tax_regime_checked_at = now
    if regime:
        # SUNAT manda sobre lo que se haya declarado a mano.
        organization.tax_regime = regime
        organization.tax_regime_source = Organization.RegimeSource.SUNAT
        fields += ["tax_regime", "tax_regime_source"]
    organization.save(update_fields=fields)
    return {"tributos": len(tributos), "regimen": regime}
