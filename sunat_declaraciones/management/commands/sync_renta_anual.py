"""Trae de e-renta las DJ anuales (F.V. 710) de una empresa conectada."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from accounts.models import Organization, SunatCredential
from sunat_declaraciones.services import sincronizar_renta_anual


class Command(BaseCommand):
    help = "Consulta en e-renta las declaraciones anuales presentadas y las guarda con su zip."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--ruc", required=True)
        parser.add_argument("--headful", action="store_true")

    def handle(self, *args, **options) -> None:
        try:
            organization = Organization.objects.get(ruc=options["ruc"])
            credential = SunatCredential.objects.get(organization=organization)
        except (Organization.DoesNotExist, SunatCredential.DoesNotExist) as exc:
            raise CommandError("Esa empresa no está conectada a SUNAT.") from exc
        r = sincronizar_renta_anual(organization.ruc, credential.sol_username, credential.password, headless=not options["headful"])
        self.stdout.write(f"{r.presentaciones} presentaciones ({r.nuevas} nuevas, {r.actualizadas} actualizadas); {r.evidencias} evidencias.")
