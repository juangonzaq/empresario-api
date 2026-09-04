"""Descargas masivas de comprobantes: un .zip por empresa y rango, entregado
contra un código de un solo uso enviado al correo de quien lo pide.

Bajar de golpe todos los comprobantes de una empresa no es lo mismo que
mirarlos en pantalla: es la información completa saliendo del sistema en un
archivo. Por eso cada exportación queda registrada —quién, con qué filtro,
cuántos documentos, cuándo se descargó— y solo se entrega con un código de
seis dígitos que llega al correo del solicitante, vence en minutos y sirve
una sola vez: un token de sesión robado no basta para llevarse el archivo.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import BaseModel

CODE_MINUTES = 10
MAX_ATTEMPTS = 5


class ExportSource(models.TextChoices):
    CPE = "cpe", "Facturas y notas"
    RHE = "rhe", "Recibos por honorarios"


class DocumentExport(BaseModel):
    account_ruc = models.CharField("RUC de la empresa", max_length=11, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="document_exports",
        null=True, on_delete=models.SET_NULL,
        help_text="Quien pidió la descarga; se conserva el registro aunque se borre la cuenta.",
    )
    source = models.CharField(max_length=5, choices=ExportSource)
    filters = models.JSONField(default=dict, blank=True)
    label = models.CharField(max_length=200, blank=True, help_text="El filtro, en palabras.")
    document_count = models.PositiveIntegerField(default=0)

    # El código nunca se guarda en claro: solo su huella, salada con el id.
    code_hash = models.CharField(max_length=64)
    attempts = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField()
    downloaded_at = models.DateTimeField(null=True, blank=True)
    zip_bytes = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["account_ruc", "-created_at"])]
        verbose_name = "descarga de comprobantes"
        verbose_name_plural = "descargas de comprobantes"

    def __str__(self) -> str:
        return f"[{self.account_ruc}] {self.label} · {self.document_count} docs"

    # ---------------------------------------------------------------- código
    def _hash(self, code: str) -> str:
        return hashlib.sha256(f"{self.pk}:{code}".encode()).hexdigest()

    @classmethod
    def issue(
        cls, *, account_ruc: str, user, source: str, filters: dict, label: str,
        document_count: int,
    ) -> tuple["DocumentExport", str]:
        """Crea la solicitud y devuelve el código en claro, que solo existe
        para ir al correo. Las solicitudes anteriores del mismo usuario y
        empresa que sigan vivas se cierran: un código viejo en la bandeja no
        debe seguir sirviendo."""
        cls.objects.filter(
            account_ruc=account_ruc, user=user, downloaded_at__isnull=True,
            expires_at__gt=timezone.now(),
        ).update(expires_at=timezone.now())
        code = f"{secrets.randbelow(10**6):06d}"
        export = cls(
            account_ruc=account_ruc, user=user, source=source, filters=filters,
            label=label[:200], document_count=document_count,
            expires_at=timezone.now() + datetime.timedelta(minutes=CODE_MINUTES),
        )
        export.code_hash = export._hash(code)
        export.save()
        return export, code

    @property
    def is_usable(self) -> bool:
        return (
            self.downloaded_at is None
            and self.expires_at > timezone.now()
            and self.attempts < MAX_ATTEMPTS
        )

    @property
    def attempts_left(self) -> int:
        return max(0, MAX_ATTEMPTS - self.attempts)

    def code_matches(self, code: str) -> bool:
        """Sin efectos: el que llama decide si consumir o anotar el fallo."""
        return bool(code) and hmac.compare_digest(self.code_hash, self._hash(code))

    def register_failed_attempt(self) -> None:
        self.attempts += 1
        self.save(update_fields=["attempts", "updated_at"])

    def consume(self, zip_bytes: int) -> None:
        self.downloaded_at = timezone.now()
        self.zip_bytes = zip_bytes
        self.save(update_fields=["downloaded_at", "zip_bytes", "updated_at"])
