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
