"""Abre la sesión de AFPnet de una empresa desde la terminal.

Es el camino de servicio: el mismo desafío que la interfaz web enseña al
empresario, pero resuelto por quien opera el sistema. Sirve para arrancar una
empresa sin pasar por el navegador y para diagnosticar cuando el portal cambia.

    python manage.py afpnet_conectar --ruc 20604442533 --usuario ADM0001

La clave no se pide por argumento a propósito: los argumentos quedan en el
historial del shell y en la lista de procesos. Se escribe cuando la pide, o se
pone en ``AFPNET_PASSWORD`` para no teclearla en cada prueba.
"""

from __future__ import annotations

import base64
import getpass
import os
import re
import subprocess
import sys
import tempfile

from django.core.management.base import BaseCommand, CommandError

from accounts.models import Organization
from afpnet.models import AfpnetSession
from afpnet.services import client


class Command(BaseCommand):
    help = "Abre la sesión de AFPnet de una empresa (el CAPTCHA lo resuelves tú)."

    def add_arguments(self, parser):
        parser.add_argument("--ruc", required=True)
        parser.add_argument("--usuario", required=True)
        parser.add_argument(
            "--intentos", type=int, default=3,
            help="Cuántas imágenes nuevas ofrecer si el CAPTCHA sale mal.",
        )

    def handle(self, *args, **opciones):
        ruc = opciones["ruc"].strip()
        usuario = opciones["usuario"].strip()

        organization = Organization.objects.filter(ruc=ruc).first()
        if organization is None:
            raise CommandError(f"No hay ninguna empresa registrada con RUC {ruc}.")

        clave = os.getenv("AFPNET_PASSWORD") or getpass.getpass(
            f"Clave de AFPnet para {usuario}@{ruc}: "
        )
        if not clave:
            raise CommandError("Sin clave no hay nada que intentar.")

        for intento in range(1, opciones["intentos"] + 1):
            self.stdout.write(f"\nIntento {intento} — pidiendo CAPTCHA…")
            desafio = client.pedir_desafio()
            ruta = self._guardar_imagen(desafio.captcha_data_uri)
            self.stdout.write(f"Imagen del CAPTCHA: {ruta}")
            self._abrir(ruta)

            texto = input("Escribe el texto de la imagen (Enter para otra): ").strip()
            if not texto:
                continue

            try:
                cookies = client.responder_desafio(
                    desafio.estado, ruc, usuario, clave, texto
                )
            except client.LoginRechazado as exc:
                self.stderr.write(self.style.WARNING(f"Rechazado: {exc}"))
                if exc.captcha:
                    continue  # otra imagen; las credenciales pueden estar bien
                raise CommandError(
                    "AFPnet rechazó las credenciales, no el CAPTCHA. Revisa "
                    "usuario y clave antes de volver a intentarlo — los "
                    "intentos fallidos pueden bloquear el usuario."
                ) from exc

            sesion, _ = AfpnetSession.objects.get_or_create(
                organization=organization, defaults={"taxpayer_id": ruc},
            )
            sesion.taxpayer_id = ruc
            sesion.marcar_activa(cookies, usuario)
            self.stdout.write(self.style.SUCCESS(
                f"\nSesión abierta y guardada para {ruc}. "
                f"Cookies: {', '.join(sorted(cookies))}"
            ))
            return

        raise CommandError("Se agotaron los intentos sin abrir sesión.")

    def _guardar_imagen(self, data_uri: str) -> str:
        cabecera, _, datos = client.limpiar_data_uri(data_uri).partition(",")
        extension = "png"
        tipo = re.search(r"image/(\w+)", cabecera)
        if tipo:
            # El portal declara gif pero manda JPEG; se respeta lo declarado
            # salvo que los bytes digan otra cosa.
            extension = tipo.group(1)
        crudo = base64.b64decode(datos)
        if crudo.startswith(b"\xff\xd8"):
            extension = "jpg"
        with tempfile.NamedTemporaryFile(
            suffix=f".{extension}", prefix="afpnet_captcha_", delete=False
        ) as archivo:
            archivo.write(crudo)
            return archivo.name

    def _abrir(self, ruta: str) -> None:
        """Abre la imagen con el visor del sistema; si no se puede, da igual:
        la ruta ya está impresa y se puede abrir a mano."""
        visor = {"darwin": "open", "linux": "xdg-open"}.get(sys.platform)
        if not visor:
            return
        try:
            subprocess.run([visor, ruta], check=False, capture_output=True)
        except OSError:
            pass
