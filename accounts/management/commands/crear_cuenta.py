"""Crea (o actualiza) una cuenta con su empresa y sus credenciales SUNAT.

Pensado para dar de alta la cuenta propia y para soporte, no para el alta
normal de clientes —esa pasa por el registro de la aplicación—.

    python manage.py crear_cuenta --email juancarlos@pattern.pe --ruc 20...

La clave SOL se guarda cifrada, igual que si la hubiera escrito el usuario en
la pantalla de conexión. No hay credenciales en el entorno de donde tomarlas:
cada empresa tiene las suyas.
"""

from __future__ import annotations

import secrets

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import (
    Membership, Organization, Role, SunatConnectionStatus, SunatCredential, User,
)


class Command(BaseCommand):
    help = "Crea una cuenta con su empresa y, opcionalmente, sus credenciales SOL."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--password", help="Si se omite, se genera una.")
        parser.add_argument("--nombres", default="")
        parser.add_argument("--apellidos", default="")
        parser.add_argument("--ruc", required=True)
        parser.add_argument("--razon-social", default="")
        parser.add_argument("--sol-usuario")
        parser.add_argument("--sol-clave")
        parser.add_argument(
            "--usuario-principal", action="store_true",
            help="Marca las credenciales como del usuario SOL principal.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        ruc = options["ruc"].strip()

        password = options["password"] or secrets.token_urlsafe(12)
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "first_name": options["nombres"],
                "last_name": options["apellidos"],
            },
        )
        user.email_verified_at = user.email_verified_at or timezone.now()
        if created or options["password"]:
            user.set_password(password)
        user.save()

        organization, org_created = Organization.objects.get_or_create(
            ruc=ruc, defaults={"name": options["razon_social"]}
        )
        if options["razon_social"]:
            organization.name = options["razon_social"]
            organization.save(update_fields=["name", "updated_at"])

        membership, _ = Membership.objects.get_or_create(
            user=user, organization=organization, defaults={"role": Role.OWNER}
        )

        sol_user = options["sol_usuario"]
        sol_pass = options["sol_clave"]
        if sol_user and sol_pass:
            credential, _ = SunatCredential.objects.get_or_create(
                organization=organization, defaults={"sol_username": sol_user}
            )
            credential.sol_username = sol_user
            credential.set_password(sol_pass)
            credential.uses_primary_user = options["usuario_principal"]
            # Pendiente, no conectada: lo confirma el primer portal que
            # responda durante la sincronización. No se declara buena una
            # credencial que nadie ha probado.
            credential.status = SunatConnectionStatus.PENDING
            credential.connected_by = user
            credential.save()
            sol_note = f"credenciales SOL guardadas (usuario {sol_user})"
        else:
            sol_note = "sin credenciales SOL"

        self.stdout.write(self.style.SUCCESS(
            f"Cuenta {'creada' if created else 'actualizada'}: {email}\n"
            f"Empresa {'creada' if org_created else 'existente'}: "
            f"{organization.ruc} · {organization.display_name}\n"
            f"Rol: {membership.get_role_display()} · {sol_note}"
        ))
        if created or options["password"]:
            self.stdout.write(f"Contraseña de acceso: {password}")
            self.stdout.write(self.style.WARNING("Cámbiala al entrar."))
