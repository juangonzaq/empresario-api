"""Una fuente que no trae nada tiene que verse como fallo, no como éxito.

Los sincronizadores de ficha RUC y REMYPE recorren una lista de RUC y anotan
los que fallan en un contador, sin levantar excepción: así una empresa caída
no interrumpe el recorrido de las demás. El trabajo de sincronización les pasa
un solo RUC, y durante un tiempo se tragó ese contador — la pantalla mostraba
«Listo — 0 consultados» en verde mientras el navegador se había caído al
arrancar. Un fallo invisible es peor que un fallo ruidoso: nadie lo mira.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from remype.models import RemypeCheck
from ruc_profile.models import RucSnapshot
from sync.sources import SourceFailed, _remype, _ruc_profile

RUC = "20604442533"


@dataclass
class Creds:
    ruc: str = RUC
    username: str = "CONSULTA1"
    password: str = "clave-sol"


@dataclass
class FakeResult:
    """Lo que devuelven los sincronizadores reales, en lo que aquí importa."""

    failed: int = 0
    checked: int = 0
    captured: int = 0


class RemypeFailureTests(TestCase):
    def test_fallo_levanta_excepcion_con_el_motivo_guardado(self):
        RemypeCheck.objects.create(
            ruc=RUC, checked_on=timezone.localdate(), succeeded=False,
            is_registered=False, changed=False,
            message="Could not open the REMYPE page: browser has been closed",
            payload={},
        )
        with patch("remype.services.RemypeSynchronizer") as sync:
            sync.return_value.run.return_value = FakeResult(failed=1)
            with self.assertRaises(SourceFailed) as capturado:
                _remype(Creds(), "manual")

        self.assertIn("browser has been closed", str(capturado.exception))

    def test_sin_motivo_guardado_cae_al_texto_generico(self):
        with patch("remype.services.RemypeSynchronizer") as sync:
            sync.return_value.run.return_value = FakeResult(failed=1)
            with self.assertRaises(SourceFailed) as capturado:
                _remype(Creds(), "manual")

        self.assertIn("REMYPE", str(capturado.exception))

    def test_sin_fallos_devuelve_el_conteo(self):
        with patch("remype.services.RemypeSynchronizer") as sync:
            sync.return_value.run.return_value = FakeResult(checked=1)
            self.assertEqual(_remype(Creds(), "manual"), {"consultados": 1})


class RucProfileFailureTests(TestCase):
    def test_fallo_levanta_excepcion_con_el_motivo_guardado(self):
        RucSnapshot.objects.create(
            ruc=RUC, captured_on=timezone.localdate(), succeeded=False,
            changed=False, error="SUNAT no respondió",
        )
        with patch("ruc_profile.services.RucProfileSynchronizer") as sync:
            sync.return_value.run.return_value = FakeResult(failed=1)
            with self.assertRaises(SourceFailed) as capturado:
                _ruc_profile(Creds(), "manual")

        self.assertIn("SUNAT no respondió", str(capturado.exception))

    def test_sin_fallos_devuelve_el_conteo(self):
        with patch("ruc_profile.services.RucProfileSynchronizer") as sync:
            sync.return_value.run.return_value = FakeResult(captured=1)
            self.assertEqual(_ruc_profile(Creds(), "manual"), {"capturados": 1})
