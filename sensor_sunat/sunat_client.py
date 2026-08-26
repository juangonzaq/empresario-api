"""Auth + HTTP + ticket pattern for SUNAT's SIRE APIs — read-only by design.

There is no SUNAT sandbox: every call hits production. Three guardrails:

* a whitelist of URL path fragments — anything else raises ``ForbiddenEndpoint``;
* a veto list of write-ish words anywhere in the URL;
* a process-wide file lock so two SUNAT operations never run concurrently.

Exact URLs and parameters were extracted from the official manuals in ``docs/``
(SIRE Ventas v30, SIRE Compras v22); section numbers below refer to them.
Deviations between manual and reality are logged in ``docs/DESVIACIONES.md``.
"""

from __future__ import annotations

import io
import logging
import os
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Read endpoints only. Every fragment here was extracted from the manuals.
ALLOWED_PATH_FRAGMENTS = [
    "/oauth2/token/",
    "/periodos",                                    # Ventas §5.2 / Compras §5.33
    "/exportapropuesta",                            # Ventas §5.18 (RVIE proposal, ticket)
    "/exportacioncomprobantepropuesta",             # Compras §5.34 (RCE proposal, ticket)
    "/consultaestadotickets",                       # Ventas §5.16 / Compras §5.31
    "/archivoreporte",                              # Ventas §5.17 / Compras §5.32
    "/casillaspropuestas",                          # Ventas §5.23 / Compras §5.41
    "/reporteinconsistencia",                       # Ventas §5.24
    "/periodoinconsistencias",                      # Ventas §5.25 / Compras §5.44 (ticket)
    "/exportarinconsistenciasporcomprobantes",      # Compras §5.44
    "/consultaReporteCumplimiento/exportardocumento",  # Ventas §5.34 / Compras §5.58
    "/resumenestadistico",                          # Ventas §5.33 / Compras §5.54
    "/constancia",                                  # Ventas §5.26 / Compras §5.49
    "/propuesta/web?",                              # Compras §5.14 FV0621 (documented PUT, read-only query)
]
# Write-ish words vetoed anywhere in a URL, per the spec. "grab" (grabar) matters:
# SIRE has /grabacreditofiscal-style write endpoints.
FORBIDDEN_URL_WORDS = ("aceptar", "registra", "upload", "elimina", "grab", "importar")

# Anexo III (Ventas manual p.80): ticket send states.
TICKET_STATES = {
    "01": "Cargado (solicitado)",
    "02": "Validando Archivo (en proceso)",
    "03": "Procesado con Errores",
    "04": "Procesado sin errores (concluido)",
    "05": "En proceso",
    "06": "Terminado",
}
# §5.17: the report file exists once codProceso/codEstadoProceso reaches 3 or 4;
# 06 "Terminado" is the other terminal state observed in the manual.
TICKET_DONE_STATES = {"3", "03", "4", "04", "6", "06"}
TICKET_ERROR_STATES = {"3", "03"}  # done, but flag it: processed WITH errors

POLL_INTERVAL_S = 15
POLL_MAX_INTERVAL_S = 120
POLL_TIMEOUT_S = 30 * 60


class ForbiddenEndpoint(RuntimeError):
    """URL is not in the read-only whitelist. Refusing to call production."""


class SunatApiError(RuntimeError):
    """Non-retryable SUNAT error (422 validation, exhausted retries, bad payload)."""

    def __init__(self, message: str, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class SunatClient:
    """Token + plain requests against SIRE, plus the ticket/poll/download dance."""

    def __init__(self, conf: dict[str, Any] | None = None):
        self.conf = conf or settings.SUNAT
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self.media_dir = Path(settings.MEDIA_SUNAT_DIR)
        self.media_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ guard
    @staticmethod
    def check_url(url: str) -> None:
        lowered = url.lower()
        for word in FORBIDDEN_URL_WORDS:
            if word in lowered:
                raise ForbiddenEndpoint(f"URL contains vetoed word '{word}': {url}")
        if not any(fragment.lower() in lowered for fragment in ALLOWED_PATH_FRAGMENTS):
            raise ForbiddenEndpoint(f"URL not in the read-only whitelist: {url}")

    # ------------------------------------------------------------------- lock
    def acquire_lock(self) -> None:
        """Never run two SUNAT operations in parallel (spec §3)."""
        self._lock_path = self.media_dir / ".sunat.lock"
        try:
            fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            age = time.time() - self._lock_path.stat().st_mtime
            if age < POLL_TIMEOUT_S:
                raise SunatApiError(
                    f"Another SUNAT operation holds {self._lock_path} "
                    f"({age:.0f}s old). Wait for it or delete the file if stale."
                )
            # A lock older than the poll ceiling is a crashed run; take it over.
            self._lock_path.unlink()
            fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)

    def release_lock(self) -> None:
        lock = getattr(self, "_lock_path", None)
        if lock and lock.exists():
            lock.unlink()

    def __enter__(self) -> "SunatClient":
        self.acquire_lock()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.release_lock()

    # ------------------------------------------------------------------ token
    def get_token(self, force: bool = False) -> str:
        """Ventas/Compras §5.1: password grant against api-seguridad.

        The manual prints ``username: {RUC} {USUARIO}``; whether the space is
        literal is ambiguous, so on an auth error the other variant is tried
        once and the outcome is logged (docs/DESVIACIONES.md).
        """
        if self._token and not force and time.time() < self._token_expires_at:
            return self._token

        conf = self.conf
        if not conf.get("CLIENT_ID") or not conf.get("CLIENT_SECRET"):
            raise SunatApiError(
                "Sin credenciales de API SUNAT: pásalas en `conf` al crear el "
                "cliente. Se generan por RUC en SOL (Empresas > Credenciales "
                "de API SUNAT); no existen credenciales globales en el entorno."
            )
        url = conf["TOKEN_URL"].format(client_id=conf["CLIENT_ID"])
        self.check_url(url)

        last_error: str = ""
        # Concatenated first: confirmed working against production (the manual's
        # spaced variant returns access_denied — docs/DESVIACIONES.md).
        for label, username in [
            ("concatenated '{RUC}{USER}'", f"{conf['RUC']}{conf['SOL_USER']}"),
            ("manual '{RUC} {USER}'", f"{conf['RUC']} {conf['SOL_USER']}"),
        ]:
            response = requests.post(
                url,
                data={
                    "grant_type": "password",
                    "scope": conf["SCOPE"],
                    "client_id": conf["CLIENT_ID"],
                    "client_secret": conf["CLIENT_SECRET"],
                    "username": username,
                    "password": conf["SOL_PASS"],
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=60,
            )
            if response.ok:
                payload = response.json()
                self._token = payload["access_token"]
                expires_in = int(payload.get("expires_in", 3600))
                # Renew at 80% of the token lifetime.
                self._token_expires_at = time.time() + expires_in * 0.8
                logger.info("SUNAT token obtained (username format: %s)", label)
                return self._token
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            logger.warning("Token request with %s failed: %s", label, last_error)

        raise SunatApiError(f"Could not obtain SIRE token. Last error: {last_error}")

    # ------------------------------------------------------------------- http
    def request(
        self, method: str, url: str, *, params: dict | None = None, stream: bool = False
    ) -> requests.Response:
        """Bearer + retries. 5xx/timeout: 3 tries (5s/15s/45s). 401: renew once.

        A 422 is raised as-is with SUNAT's JSON (cod/msg/errors[]): it means our
        request is wrong, so retrying would only repeat the mistake.
        """
        self.check_url(url)
        renewed = False
        for attempt, wait in enumerate((5, 15, 45, None)):
            response = self.session.request(
                method,
                url,
                params=params,
                headers={
                    "Authorization": f"Bearer {self.get_token()}",
                    "Content-Type": "application/json",
                },
                timeout=60,
                stream=stream,
            )
            if response.status_code == 401 and not renewed:
                renewed = True
                self.get_token(force=True)
                continue
            if response.status_code == 422:
                try:
                    payload = response.json()
                except ValueError:
                    payload = {"raw": response.text[:500]}
                raise SunatApiError(
                    f"SUNAT 422 on {url}: {payload}", status=422, payload=payload
                )
            if response.status_code >= 500:
                if wait is None:
                    raise SunatApiError(
                        f"SUNAT 5xx after retries on {url}: {response.text[:300]}",
                        status=response.status_code,
                    )
                logger.warning(
                    "SUNAT %s on %s (attempt %d), retrying in %ds",
                    response.status_code, url, attempt + 1, wait,
                )
                time.sleep(wait)
                continue
            if not response.ok:
                raise SunatApiError(
                    f"SUNAT {response.status_code} on {url}: {response.text[:300]}",
                    status=response.status_code,
                )
            return response
        raise SunatApiError(f"Unreachable retry state for {url}")  # pragma: no cover

    def get_json(self, url: str, **params: Any) -> Any:
        response = self.request("GET", url, params=params or None)
        if not response.content:
            return None
        return response.json()

    def get_bytes(self, url: str, **params: Any) -> bytes:
        return self.request("GET", url, params=params or None).content

    def put_json(self, url: str, **params: Any) -> Any:
        # FV0621 (Compras §5.14) is documented as PUT despite being a query.
        response = self.request("PUT", url, params=params or None)
        return response.json() if response.content else None

    # ----------------------------------------------------------------- ticket
    def fetch_ticket_result(
        self,
        dispatch_fn: Callable[[], str],
        *,
        book_code: str,
        period: str,
        endpoint_label: str,
    ) -> Path:
        """Run the SIRE async pattern: dispatch → poll → download → save raw.

        Returns the directory that holds every downloaded (and, when possible,
        extracted) file. Raw bytes are always saved before any parsing.
        """
        ticket = dispatch_fn()
        if not ticket:
            raise SunatApiError(f"{endpoint_label}: dispatch returned no numTicket")
        logger.info("%s: ticket %s dispatched for %s", endpoint_label, ticket, period)

        record = self._poll_ticket(ticket, book_code=book_code, period=period)
        state = str(record.get("codEstadoProceso") or "")
        if state in TICKET_ERROR_STATES:
            logger.warning(
                "%s: ticket %s finished 'Procesado con Errores'; downloading report anyway",
                endpoint_label, ticket,
            )

        target_dir = (
            self.media_dir / endpoint_label / period
            / datetime.now().strftime("%Y%m%dT%H%M%S")
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        self._download_report_files(record, book_code=book_code, target_dir=target_dir)
        return target_dir

    def _poll_ticket(self, ticket: str, *, book_code: str, period: str) -> dict:
        """Ventas §5.16 / Compras §5.31, states per Anexo III."""
        url = (
            f"{self.conf['BASE']}/rvierce/gestionprocesosmasivos/web/masivo/"
            "consultaestadotickets"
        )
        deadline = time.time() + POLL_TIMEOUT_S
        interval = POLL_INTERVAL_S
        while time.time() < deadline:
            payload = self.get_json(
                url,
                perIni=period, perFin=period, page=1, perPage=20,
                numTicket=ticket, codLibro=book_code, codOrigenEnvio=2,
            )
            records = (payload or {}).get("registros") or []
            record = next(
                (r for r in records if str(r.get("numTicket")) == str(ticket)),
                records[0] if records else None,
            )
            state = str((record or {}).get("codEstadoProceso") or "")
            logger.info(
                "ticket %s state=%s (%s)", ticket, state,
                TICKET_STATES.get(state.zfill(2), "?"),
            )
            if record and state in TICKET_DONE_STATES:
                return record
            time.sleep(interval)
            interval = min(interval * 2, POLL_MAX_INTERVAL_S)
        raise SunatApiError(f"Ticket {ticket} still not finished after 30 minutes")

    def _download_report_files(
        self, record: dict, *, book_code: str, target_dir: Path
    ) -> None:
        """Ventas §5.17 / Compras §5.32. ZIPs may come partitioned (-01, -02...):
        download every part, concatenate, then extract."""
        url = (
            f"{self.conf['BASE']}/rvierce/gestionprocesosmasivos/web/masivo/"
            "archivoreporte"
        )
        files = record.get("archivoReporte") or []
        if not files:
            raise SunatApiError(f"Ticket {record.get('numTicket')} has no report files")

        blobs: list[bytes] = []
        for entry in sorted(files, key=lambda e: str(e.get("nomArchivoReporte"))):
            name = entry.get("nomArchivoReporte")
            content = self.get_bytes(
                url,
                nomArchivoReporte=name,
                # The manual: if 5.16 returns codTipoAchivoReporte null, send null.
                codTipoArchivoReporte=entry.get("codTipoAchivoReporte")
                or entry.get("codTipoArchivoReporte"),
                codLibro=book_code,
                perTributario=record.get("perTributario"),
                codProceso=record.get("codProceso"),
                numTicket=record.get("numTicket"),
            )
            (target_dir / str(name)).write_bytes(content)
            blobs.append(content)
            logger.info("downloaded %s (%d bytes)", name, len(content))

        assembled = b"".join(blobs)
        try:
            with zipfile.ZipFile(io.BytesIO(assembled)) as archive:
                archive.extractall(target_dir)
                logger.info("extracted: %s", ", ".join(archive.namelist()))
        except zipfile.BadZipFile:
            # Not a zip (single TXT, or an odd partition scheme): raw parts are
            # already on disk, which is what matters.
            logger.info("report is not a readable zip; raw parts kept in %s", target_dir)

    # ------------------------------------------------- concrete read services
    def fetch_periods(self, book_code: str) -> list[dict]:
        """Ventas §5.2 / Compras §5.33: enabled years and months per book."""
        url = f"{self.conf['BASE']}/rvierce/padron/web/omisos/{book_code}/periodos"
        return self.get_json(url) or []

    def dispatch_rvie_proposal(self, period: str) -> str:
        """Ventas §5.18: returns a numTicket for the RVIE proposal TXT."""
        url = (
            f"{self.conf['BASE']}/rvie/propuesta/web/propuesta/"
            f"{period}/exportapropuesta"
        )
        payload = self.get_json(url, codTipoArchivo=0)
        return (payload or {}).get("numTicket", "")

    def dispatch_rce_proposal(self, period: str) -> str:
        """Compras §5.34: returns a numTicket for the RCE proposal TXT."""
        url = (
            f"{self.conf['BASE']}/rce/propuesta/web/propuesta/"
            f"{period}/exportacioncomprobantepropuesta"
        )
        payload = self.get_json(url, codTipoArchivo=0, codOrigenEnvio=2)
        return (payload or {}).get("numTicket", "")

    def dispatch_rvie_inconsistencies(self, period: str) -> str:
        """Ventas §5.25: ticket for per-document inconsistencies."""
        url = (
            f"{self.conf['BASE']}/rvie/inconsistencias/web/periodoinconsistencias/"
            f"{period}/exporta"
        )
        payload = self.get_json(url, codTipoArchivo=0)
        return (payload or {}).get("numTicket", "")

    def dispatch_rce_inconsistencies(self, period: str) -> str:
        """Compras §5.44: ticket for per-document inconsistencies."""
        url = (
            f"{self.conf['BASE']}/rce/inconsistencias/web/periodoinconsistencias/"
            f"{period}/{self.conf['COD_LIBRO_RCE']}/exportarinconsistenciasporcomprobantes"
        )
        payload = self.get_json(url, codTipoArchivo=0, codOrigenEnvio=2)
        return (payload or {}).get("numTicket", "")

    def fetch_boxes_report(self, period: str, report_type: int = 2) -> bytes:
        """Ventas §5.23 / Compras §5.41: casillas report, direct TXT download."""
        url = (
            f"{self.conf['BASE']}/rvierce/casillas/e/casillaspropuestas/"
            f"{period}/reporte/{report_type}/0"
        )
        return self.get_bytes(url)

    def fetch_compliance_report(self, period: str, book_code: str) -> dict:
        """Ventas §5.34 / Compras §5.58: {archivoPdf: base64, nombreArchivoPdf}."""
        url = (
            f"{self.conf['BASE']}/rvierce/cumplimiento/web/omisos/"
            f"{period}/{book_code}/consultaReporteCumplimiento/exportardocumento"
        )
        return self.get_json(url) or {}

    def fetch_statistics(self, period: str, book_code: str) -> bytes:
        """Ventas §5.33 / Compras §5.54: 'Razón Social|Monto|Porcentaje' file."""
        base = f"{self.conf['BASE']}/rvierce/estadistica/web/resumenestadistico"
        if book_code == self.conf["COD_LIBRO_RVIE"]:
            url = f"{base}/exportarvie"
        else:
            url = f"{base}/exporta"
        return self.get_bytes(
            url,
            numRuc=self.conf["RUC"], perTributario=period,
            codTipoArchivo=0, codTipoReporte=1, codLibro=book_code,
        )

    def fetch_fv0621(self, period: str) -> Any:
        """Compras §5.14: FV0621 figures (prorrata, RCF, CFE). Documented as PUT."""
        url = f"{self.conf['BASE']}/rce/propuesta/web?periodoSeleccionado={period}&tipoInfo=FV0621"
        return self.put_json(url)

    def save_artifact(self, endpoint_label: str, period: str, content: bytes, filename: str) -> Path:
        """Persist a directly-downloaded raw file under MEDIA_SUNAT_DIR."""
        target_dir = (
            self.media_dir / endpoint_label / period
            / datetime.now().strftime("%Y%m%dT%H%M%S")
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / filename
        path.write_bytes(content)
        return path
