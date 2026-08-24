from __future__ import annotations

from rest_framework import serializers

from .models import SyncJob


class SyncJobSerializer(serializers.ModelSerializer):
    progress_pct = serializers.IntegerField(read_only=True)
    total_steps = serializers.IntegerField(read_only=True)
    finished_steps = serializers.IntegerField(read_only=True)
    current_step = serializers.DictField(read_only=True, allow_null=True)
    ruc = serializers.CharField(source="organization.ruc", read_only=True)
    steps = serializers.SerializerMethodField()

    def get_steps(self, job: SyncJob) -> list[dict]:
        """Los pasos con la marca de si se pueden relanzar solos.

        La decide el modelo, no el frontend: así el botón «Reintentar» aparece
        exactamente cuando el endpoint lo aceptaría, en lugar de repetir la
        regla en dos sitios y que se desincronicen.
        """
        return [
            {**step, "retryable": job.can_retry(step.get("key", ""))}
            for step in job.steps
        ]

    class Meta:
        model = SyncJob
        fields = (
            "id", "ruc", "kind", "status", "steps", "progress_pct", "total_steps",
            "finished_steps", "current_step", "started_at", "finished_at",
            "error", "created_at",
        )
        read_only_fields = fields


class SyncJobHistorySerializer(serializers.ModelSerializer):
    """Vista compacta para el historial: no lleva todos los pasos, solo lo que
    la lista necesita —tipo, estado, avance, quién lo pidió y si hubo fallas—."""

    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    progress_pct = serializers.IntegerField(read_only=True)
    total_steps = serializers.IntegerField(read_only=True)
    finished_steps = serializers.IntegerField(read_only=True)
    is_manual = serializers.SerializerMethodField()
    requested_by = serializers.SerializerMethodField()
    failed_steps = serializers.SerializerMethodField()

    class Meta:
        model = SyncJob
        fields = (
            "id", "kind", "kind_label", "status", "status_label", "progress_pct",
            "total_steps", "finished_steps", "is_manual", "requested_by",
            "failed_steps", "error", "started_at", "finished_at", "created_at",
        )
        read_only_fields = fields

    def get_is_manual(self, job: SyncJob) -> bool:
        from .models import JobKind

        return job.kind == JobKind.MANUAL

    def get_requested_by(self, job: SyncJob) -> str | None:
        return job.requested_by.email if job.requested_by_id else None

    def get_failed_steps(self, job: SyncJob) -> list[str]:
        from .models import StepStatus

        return [
            s.get("label") or s.get("key")
            for s in job.steps
            if s.get("status") in (StepStatus.FAILED, StepStatus.SKIPPED)
        ]
