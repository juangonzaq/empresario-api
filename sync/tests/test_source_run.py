"""Relanzar una fuente a pedido.

Existe porque `retry_step` no sirve para esto: aquel repara un paso que quedó
fallido y se niega en cualquier otro caso, así que el botón «sincronizar» de
cada sección quedaba apagado justo cuando la última sincronización había ido
bien —que es casi siempre—.
"""

from __future__ import annotations

from unittest.mock import patch

from django.urls import reverse

from core.testing import TenantAPITestCase
from sync.models import JobKind, JobStatus, StepStatus, SyncJob
from sync.sources import initial_steps

RUC = "20604442533"


def _url(key: str) -> str:
    return reverse("sync:source-run", kwargs={"key": key})


class RelanzarFuenteTests(TenantAPITestCase):
    RUC = RUC

    def setUp(self):
        self.job = SyncJob.objects.create(
            organization=self.organization,
            kind=JobKind.MANUAL,
            steps=initial_steps(JobKind.MANUAL),
            status=JobStatus.DONE,
        )
        for paso in self.job.steps:
            paso["status"] = StepStatus.DONE
        self.job.save(update_fields=["steps"])

    def test_una_fuente_completa_se_puede_relanzar(self):
        """Lo que `retry_step` prohíbe y aquí es el caso normal."""
        self.assertFalse(self.job.can_retry("sunafil"))

        with patch("sync.tasks.run_sync_step.apply_async") as encolar:
            respuesta = self.client.post(_url("sunafil"))

        self.assertEqual(respuesta.status_code, 202)
        encolar.assert_called_once_with((str(self.job.id), "sunafil"))

    def test_el_paso_vuelve_a_quedar_pendiente(self):
        with patch("sync.tasks.run_sync_step.apply_async"):
            self.client.post(_url("sunafil"))

        self.job.refresh_from_db()
        paso = self.job.step("sunafil")
        self.assertEqual(paso["status"], StepStatus.PENDING)
        self.assertTrue(self.job.is_unfinished)

    def test_no_se_relanza_con_un_trabajo_en_marcha(self):
        """SUNAT admite una sesión por usuario SOL: dos scrapeos a la vez se
        estorban entre sí."""
        self.job.status = JobStatus.RUNNING
        self.job.save(update_fields=["status"])

        with patch("sync.tasks.run_sync_step.apply_async") as encolar:
            respuesta = self.client.post(_url("sunafil"))

        self.assertEqual(respuesta.status_code, 409)
        encolar.assert_not_called()

    def test_una_fuente_inventada_se_rechaza(self):
        respuesta = self.client.post(_url("inventada"))

        self.assertEqual(respuesta.status_code, 409)
        self.assertIn("no existe", respuesta.json()["detail"])

    def test_sin_sincronizacion_previa_se_pide_una_completa(self):
        SyncJob.objects.all().delete()

        respuesta = self.client.post(_url("sunafil"))

        self.assertEqual(respuesta.status_code, 409)
        self.assertIn("sincronización completa", respuesta.json()["detail"])

    def test_el_broker_caido_no_tumba_la_peticion(self):
        """El paso queda encolado y se puede volver a pulsar; devolver 500 no
        arreglaría Redis y sí perdería el gesto del usuario."""
        with patch("sync.tasks.run_sync_step.apply_async",
                   side_effect=OSError("redis abajo")):
            respuesta = self.client.post(_url("sunafil"))

        self.assertEqual(respuesta.status_code, 202)

    def test_solo_lectura_no_puede_relanzar(self):
        from accounts.models import Membership, Role

        Membership.objects.filter(
            user=self.user, organization=self.organization
        ).update(role=Role.VIEWER)

        respuesta = self.client.post(_url("sunafil"))

        self.assertEqual(respuesta.status_code, 403)


class FuenteNuevaTests(TenantAPITestCase):
    """Una fuente registrada después de que la empresa ya sincronizara.

    Sin esto quedaba inalcanzable: su paso no estaba en el último trabajo, así
    que el botón devolvía 409 para siempre y solo una sincronización completa
    lo arreglaba.
    """

    RUC = RUC

    def setUp(self):
        self.job = SyncJob.objects.create(
            organization=self.organization,
            kind=JobKind.MANUAL,
            steps=[s for s in initial_steps(JobKind.MANUAL) if s["key"] != "afpnet"],
            status=JobStatus.DONE,
        )

    def test_el_paso_se_anade_al_trabajo_existente(self):
        self.assertIsNone(self.job.step("afpnet"))

        with patch("sync.tasks.run_sync_step.apply_async") as encolar:
            respuesta = self.client.post(_url("afpnet"))

        self.assertEqual(respuesta.status_code, 202)
        encolar.assert_called_once_with((str(self.job.id), "afpnet"))
        self.job.refresh_from_db()
        self.assertIsNotNone(self.job.step("afpnet"))
