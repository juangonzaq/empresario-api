"""Trae de SOL las declaraciones y pagos de una empresa conectada."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from accounts.models import Organization, SunatCredential
from sunat_declaraciones.services import sincronizar


class Command(BaseCommand):
    help = "Consulta en SOL las declaraciones y pagos presentados y los guarda."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--ruc", required=True)
        parser.add_argument("--desde", help="Periodo AAAAMM inicial (por defecto, los últimos 3).")
        parser.add_argument("--hasta", help="Periodo AAAAMM final (por defecto, el mes actual).")
        parser.add_argument("--inicial", action="store_true", help="Carga larga: 36 periodos.")
        parser.add_argument("--headful", action="store_true", help="Muestra el navegador.")

    def handle(self, *args, **options) -> None:
        try:
            organization = Organization.objects.get(ruc=options["ruc"])
            credential = SunatCredential.objects.get(organization=organization)
        except (Organization.DoesNotExist, SunatCredential.DoesNotExist) as exc:
            raise CommandError("Esa empresa no está conectada a SUNAT.") from exc
        resultado = sincronizar(
            organization.ruc, credential.sol_username, credential.password,
            desde=options.get("desde"), hasta=options.get("hasta"),
            inicial=options["inicial"], headless=not options["headful"],
        )
        self.stdout.write(
            f"{resultado.filas} filas ({resultado.nuevas} nuevas, {resultado.actualizadas} actualizadas); "
            f"{resultado.periodos_declarados} periodos con 621; {resultado.evidencias} evidencias."
        )
