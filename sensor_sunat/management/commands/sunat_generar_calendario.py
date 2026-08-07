"""Genera el calendario tributario .ics de un RUC sin pasar por HTTP.

Uso: python manage.py sunat_generar_calendario --ruc 20604442533 --salida /tmp/cal.ics
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from sensor_sunat.calendario import a_ics, eventos_para, grupo_de, validar_ruc


class Command(BaseCommand):
    help = "Genera el calendario tributario (.ics) de un RUC — mismo motor que /api/calendario/"

    def add_arguments(self, parser):
        parser.add_argument("--ruc", required=True)
        parser.add_argument("--salida", required=True, help="Ruta del archivo .ics a escribir")
        parser.add_argument("--sin-planilla", action="store_true", help="Omitir PLAME/AFP/CTS/grati")
        parser.add_argument("--bc", action="store_true", help="Buen Contribuyente (columna BC)")
        parser.add_argument("--regimen", default="RMT", choices=["RUS", "RER", "RMT", "RG"])

    def handle(self, *args, **opts):
        ruc = opts["ruc"].strip()
        if not validar_ruc(ruc):
            raise CommandError(f"RUC inválido: {ruc}")
        ev = eventos_para(
            ruc,
            planilla=not opts["sin_planilla"],
            bc=opts["bc"],
            regimen=opts["regimen"],
        )
        salida = Path(opts["salida"])
        salida.write_text(a_ics(ruc, ev), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(
            f"OK: {len(ev)} eventos (grupo {grupo_de(ruc)}) → {salida}"
        ))
