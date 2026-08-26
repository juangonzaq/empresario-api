"""Credenciales SOL para los comandos de mantenimiento que se corren a mano.

Ya no existen credenciales globales en el entorno: cada empresa guarda las
suyas cifradas (accounts.SunatCredential). Los comandos reciben el RUC de una
empresa registrada y usan su credencial guardada; ``--username``/``--password``
siguen aceptándose juntos para probar una credencial que aún no se ha guardado.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import CommandError

from sync.services import Credentials, NotConnected, credentials_for


def add_sol_arguments(parser) -> None:
    parser.add_argument(
        "--ruc", "--taxpayer-id", dest="taxpayer_id", required=True,
        help="RUC de una empresa registrada; se usan sus credenciales SOL guardadas.",
    )
    parser.add_argument(
        "--username",
        help="Usuario SOL explícito (junto con --password, en lugar del guardado).",
    )
    parser.add_argument(
        "--password",
        help="Clave SOL explícita (junto con --username, en lugar de la guardada).",
    )


def sol_credentials(options: dict[str, Any]) -> Credentials:
    from accounts.models import Organization

    ruc = (options["taxpayer_id"] or "").strip()
    username, password = options.get("username"), options.get("password")
    if username or password:
        if not (username and password):
            raise CommandError("--username y --password van juntos.")
        return Credentials(ruc=ruc, username=username, password=password)

    organization = Organization.objects.filter(ruc=ruc).first()
    if organization is None:
        raise CommandError(
            f"No hay ninguna empresa registrada con RUC {ruc}. "
            "Regístrala o pasa --username y --password explícitos."
        )
    try:
        return credentials_for(organization)
    except NotConnected as exc:
        raise CommandError(str(exc)) from exc
