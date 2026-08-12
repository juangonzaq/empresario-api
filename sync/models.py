"""El trabajo de sincronización de una empresa y su avance.

Conectar SUNAT dispara media docena de scrapeos que juntos tardan minutos: el
usuario necesita ver *qué* se está trayendo, no una ruleta. Cada paso deja su
propio estado en ``steps``, y el frontend consulta el trabajo hasta que
termina.

Los pasos corren en serie a propósito. Los portales de SUNAT admiten una sesión
por usuario SOL, así que lanzarlos en paralelo con las mismas credenciales se
pisa a sí mismo.
"""

from __future__ import annotations

import datetime

from django.db import models
from django.utils import timezone

from accounts.models import Organization, User
from core.models import BaseModel


class JobStatus(models.TextChoices):
    QUEUED = "en_cola", "En cola"
    RUNNING = "ejecutando", "Ejecutando"
    DONE = "completo", "Completo"
    PARTIAL = "parcial", "Completo con fallas"
    FAILED = "fallido", "Fallido"


class StepStatus(models.TextChoices):
    PENDING = "pendiente", "Pendiente"
    RUNNING = "ejecutando", "Ejecutando"
    DONE = "completo", "Completo"
    FAILED = "fallido", "Fallido"
    SKIPPED = "omitido", "Omitido"


class JobKind(models.TextChoices):
    INITIAL = "inicial", "Primera carga"
    DAILY = "diaria", "Diaria"
    MONTHLY = "mensual", "Mensual"
    MANUAL = "manual", "A pedido"


# Un trabajo completo tiene 2 horas de límite en Celery. Pasado ese margen
# holgado, si sigue «en cola» o «ejecutando» es que nadie lo tomó o el worker
# murió por el camino.
STALE_AFTER = datetime.timedelta(hours=3)

STALE_MESSAGE = (
    "La sincronización quedó a medias (el proceso no respondió). Puedes "
    "volver a lanzarla."
)

# Un paso que no trajo nada se puede volver a lanzar solo, sin repetir los
# nueve que sí funcionaron. Los omitidos entran también: se saltan cuando el
# login falla o cuando el trabajo se abandona, y son justo los que interesa
# reintentar una vez arreglada la causa.
RETRYABLE_STEP_STATUSES = frozenset({StepStatus.FAILED, StepStatus.SKIPPED})


class SyncJobQuerySet(models.QuerySet):
    def active(self) -> "SyncJobQuerySet":
        """En cola o ejecutando, y todavía dentro de plazo.

        El plazo importa: sin él, un trabajo abandonado bloquea a su empresa
        para siempre, porque tanto el arranque manual como el programado se
        saltan las empresas con trabajo activo.

        El plazo se mide contra ``updated_at``, no contra el alta: cada paso
        que arranca o termina toca la fila, así que un trabajo que avanza
        nunca se da por abandonado aunque lleve horas, y uno cuyo worker murió
        deja de tocarla y cae solo. Con ``created_at`` un reintento de un paso
        suelto sobre un trabajo de ayer nacía ya vencido y ``reclaim_stale``
        lo mataba antes de que el worker lo tomara.
        """
        return self.unfinished().filter(updated_at__gte=timezone.now() - STALE_AFTER)

    def unfinished(self) -> "SyncJobQuerySet":
        return self.filter(status__in=[JobStatus.QUEUED, JobStatus.RUNNING])

    def stale(self) -> "SyncJobQuerySet":
        return self.unfinished().filter(updated_at__lt=timezone.now() - STALE_AFTER)


class SyncJob(BaseModel):
    organization = models.ForeignKey(
        Organization, related_name="sync_jobs", on_delete=models.CASCADE
    )
    requested_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="sync_jobs",
    )
    kind = models.CharField(
        max_length=10, choices=JobKind, default=JobKind.INITIAL,
        help_text="Qué disparó el trabajo; determina qué fuentes se consultan.",
    )
    status = models.CharField(max_length=12, choices=JobStatus, default=JobStatus.QUEUED)
    # [{"key", "label", "status", "detail", "started_at", "finished_at"}]
    steps = models.JSONField(default=list, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    objects = SyncJobQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.organization.ruc} · {self.get_status_display()}"

    # ── Avance ──

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    @property
    def finished_steps(self) -> int:
        done = {StepStatus.DONE, StepStatus.FAILED, StepStatus.SKIPPED}
        return sum(1 for s in self.steps if s.get("status") in done)

    @property
    def progress_pct(self) -> int:
        if not self.steps:
            return 0
        return round(self.finished_steps / len(self.steps) * 100)

    @property
    def current_step(self) -> dict | None:
        for step in self.steps:
            if step.get("status") == StepStatus.RUNNING:
                return step
        return None

    def step(self, key: str) -> dict | None:
        """El paso con esa clave, o None si este trabajo no lo incluye.

        Es público porque los servicios necesitan preguntar si un trabajo
        contiene una fuente antes de intentar relanzarla: un trabajo de una
        cadencia distinta puede no tenerla.
        """
        return next((s for s in self.steps if s.get("key") == key), None)

    # Alias interno histórico: el resto del modelo lo usa por todas partes.
    _step = step

    def mark_step(self, key: str, status: str, detail: str = "") -> None:
        step = self._step(key)
        if step is None:
            return
        step["status"] = status
        if detail:
            step["detail"] = detail
        stamp = timezone.now().isoformat()
        if status == StepStatus.RUNNING:
            step["started_at"] = stamp
        elif status in (StepStatus.DONE, StepStatus.FAILED, StepStatus.SKIPPED):
            step["finished_at"] = stamp
        self.save(update_fields=["steps", "updated_at"])

    @property
    def is_unfinished(self) -> bool:
        return self.status in (JobStatus.QUEUED, JobStatus.RUNNING)

    def can_retry(self, key: str) -> bool:
        """¿Se puede relanzar este paso por su cuenta ahora mismo?

        Es la misma regla que aplica la vista al aceptar el reintento, y la
        que el serializador manda al frontend para decidir si pinta el botón:
        una sola fuente de verdad evita que aparezca un botón que el API
        rechazaría.
        """
        if self.is_unfinished:
            return False
        step = self._step(key)
        return step is not None and step.get("status") in RETRYABLE_STEP_STATUSES

    def reopen_step(self, key: str) -> None:
        """Deja el paso listo para volver a correr y reabre el trabajo.

        El trabajo vuelve a «en cola» para que la pantalla retome el sondeo y
        para que nadie encole otra sincronización encima mientras este paso
        corre: SUNAT admite una sesión por usuario SOL.
        """
        step = self._step(key)
        if step is None:
            return
        step.update({
            "status": StepStatus.PENDING,
            "detail": "",
            "started_at": None,
            "finished_at": None,
        })
        self.status = JobStatus.QUEUED
        # El motivo del fallo anterior ya no describe lo que está pasando;
        # `finish` lo volverá a escribir si el reintento vuelve a caerse.
        self.error = ""
        self.finished_at = None
        self.save(update_fields=[
            "steps", "status", "error", "finished_at", "updated_at",
        ])

    def start(self) -> None:
        self.status = JobStatus.RUNNING
        self.started_at = timezone.now()
        self.save(update_fields=["status", "started_at", "updated_at"])

    def finish(self, error: str = "") -> None:
        failed = [s for s in self.steps if s.get("status") == StepStatus.FAILED]
        done = [s for s in self.steps if s.get("status") == StepStatus.DONE]
        if error and not done:
            self.status = JobStatus.FAILED
        elif failed:
            self.status = JobStatus.PARTIAL
        else:
            self.status = JobStatus.DONE
        self.error = error
        self.finished_at = timezone.now()
        self.save(update_fields=["status", "error", "finished_at", "updated_at"])
