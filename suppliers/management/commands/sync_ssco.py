"""Descarga a mano el padrón SSCO de SUNAT (lo que hace el cron mensual)."""

from django.core.management.base import BaseCommand, CommandError

from suppliers.services.ssco import PadronSscoError, sincronizar_padron


class Command(BaseCommand):
    help = "Descarga y guarda el padrón de Sujetos Sin Capacidad Operativa de SUNAT."

    def handle(self, *args, **options):
        try:
            r = sincronizar_padron()
        except PadronSscoError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"Padrón SSCO: {r.total} RUC — {r.nuevos} nuevos, "
            f"{r.actualizados} actualizados, {r.retirados} retirados."
        ))
