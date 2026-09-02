"""Admin de sincronizaciones: hecho para diagnosticar sin ssh.

Un paso caído guarda dos cosas: la frase que vio el cliente (``detail``) y el
error crudo con traceback (``debug``). Aquí se ven las dos, con el traceback
plegado para que la vista no sea una pared de texto.
"""

from datetime import datetime

from django.contrib import admin
from django.utils.html import format_html, format_html_join

from core.admin import filtro_empresa

from .models import StepStatus, SyncJob

_ICONO = {
    StepStatus.DONE: "✅",
    StepStatus.FAILED: "❌",
    StepStatus.SKIPPED: "⏭️",
    StepStatus.RUNNING: "⏳",
    StepStatus.PENDING: "·",
}


def _duracion(started: str | None, finished: str | None) -> str:
    if not started or not finished:
        return "—"
    seconds = int((
        datetime.fromisoformat(finished) - datetime.fromisoformat(started)
    ).total_seconds())
    return f"{seconds // 60}m {seconds % 60:02d}s"


@admin.register(SyncJob)
class SyncJobAdmin(admin.ModelAdmin):
    list_display = (
        "organization", "kind", "status", "avance", "fallas", "duracion",
        "created_at",
    )
    list_filter = (filtro_empresa("organization__ruc"), "status", "kind")
    search_fields = ("organization__ruc", "organization__name")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    fields = (
        "organization", "requested_by", "kind", "status", "error",
        "started_at", "finished_at", "pasos",
    )
    readonly_fields = fields

    @admin.display(description="avance")
    def avance(self, job: SyncJob) -> str:
        return f"{job.finished_steps}/{job.total_steps}"

    @admin.display(description="fallas")
    def fallas(self, job: SyncJob) -> str:
        caidos = [
            s.get("label") or s.get("key", "?")
            for s in job.steps if s.get("status") == StepStatus.FAILED
        ]
        return ", ".join(caidos) if caidos else "—"

    @admin.display(description="duración")
    def duracion(self, job: SyncJob) -> str:
        if not job.started_at or not job.finished_at:
            return "—"
        seconds = int((job.finished_at - job.started_at).total_seconds())
        return f"{seconds // 60}m {seconds % 60:02d}s"

    @admin.display(description="pasos")
    def pasos(self, job: SyncJob):
        filas = format_html_join(
            "", (
                "<tr style='vertical-align:top'>"
                "<td style='padding:4px 8px'>{}</td>"
                "<td style='padding:4px 8px;white-space:nowrap'><b>{}</b></td>"
                "<td style='padding:4px 8px;white-space:nowrap'>{}</td>"
                "<td style='padding:4px 8px'>{}{}</td>"
                "</tr>"
            ),
            (
                (
                    _ICONO.get(step.get("status"), "·"),
                    step.get("label") or step.get("key", "?"),
                    _duracion(step.get("started_at"), step.get("finished_at")),
                    step.get("detail", ""),
                    format_html(
                        "<details><summary style='cursor:pointer'>error crudo</summary>"
                        "<pre style='white-space:pre-wrap;font-size:11px;"
                        "max-width:80ch'>{}</pre></details>",
                        step["debug"],
                    ) if step.get("debug") else "",
                )
                for step in job.steps
            ),
        )
        return format_html("<table>{}</table>", filas)
