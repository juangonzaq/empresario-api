"""Relanzar una fuente a pedido.

Existe porque `retry_step` no sirve para esto: aquel repara un paso que quedó
fallido y se niega en cualquier otro caso, así que el botón «sincronizar» de
cada sección quedaba apagado justo cuando la última sincronización había ido
bien —que es casi siempre—.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

from django.urls import reverse

from core.testing import TenantAPITestCase
from sync.models import JobKind, JobStatus, StepStatus, SyncJob
from sync.services import Credentials
from sync.sources import Cadence, SOURCES_BY_KEY, initial_steps

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
        # Con cadencia «nuevos»: el botón de una sección pregunta si hay
        # algo nuevo, no pide que se recorra el histórico otra vez.
        encolar.assert_called_once_with((str(self.job.id), "sunafil", "nuevos"))

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
        encolar.assert_called_once_with((str(self.job.id), "afpnet", "nuevos"))
        self.job.refresh_from_db()
        self.assertIsNotNone(self.job.step("afpnet"))


class SoloLoNuevoTests(TenantAPITestCase):
    """El botón de una sección trae lo nuevo; no vuelve a recorrer el histórico.

    Los comprobantes son la fuente donde esto se nota: con la cadencia del
    trabajo —que en una empresa recién conectada es «inicial»— el paso caminaba
    hacia atrás mes a mes hasta encontrar tres vacíos seguidos. Pulsar «traer
    nuevos» en Finanzas costaba así minutos de espera y decenas de consultas a
    SUNAT para volver a guardar lo que ya estaba.
    """

    RUC = RUC

    def setUp(self):
        from accounts.models import SunatCredential

        credencial = SunatCredential.objects.create(
            organization=self.organization, sol_username="CONSULTA1",
        )
        credencial.set_password("clave-sol")
        credencial.save()

        self.job = SyncJob.objects.create(
            organization=self.organization,
            kind=JobKind.INITIAL,          # el trabajo con el que se conectó
            steps=initial_steps(JobKind.INITIAL),
            status=JobStatus.DONE,
        )

    def _cadencia_recibida(self, key: str) -> str:
        """Qué cadencia le llega a la fuente cuando se pide desde la sección.

        La fuente se sustituye por una copia espía —`Source` es un dataclass
        congelado— y la tarea se ejecuta en el momento en vez de encolarse.
        """
        recibidas: list[str] = []

        def espiar(_credenciales, cadencia):
            recibidas.append(cadencia)
            return {}

        def encolar(args):
            from sync.services import execute_step

            execute_step(SyncJob.objects.get(pk=args[0]), args[1], args[2])

        espia = replace(SOURCES_BY_KEY[key], run=espiar)
        with patch("sync.tasks.run_sync_step.apply_async", side_effect=encolar), \
                patch.dict(SOURCES_BY_KEY, {key: espia}):
            self.client.post(_url(key))
        return recibidas[0]

    def test_la_fuente_recibe_nuevos_y_no_la_del_trabajo(self):
        self.assertEqual(self._cadencia_recibida("cpe"), Cadence.NEW)

    def test_con_esa_cadencia_los_comprobantes_no_hacen_backfill(self):
        """La prueba de verdad: qué método del sincronizador se llama."""
        from sync.sources import _cpe

        with patch("sunat_cpe.services.CpePortalClient"), \
                patch("sunat_cpe.services.CpeSynchronizer") as sincronizador:
            _cpe(Credentials(ruc=RUC, username="u", password="c"), Cadence.NEW)

        instancia = sincronizador.return_value
        instancia.backfill.assert_not_called()
        instancia.sync_periods.assert_called_once()

    def test_la_sincronizacion_completa_sí_lo_hace(self):
        """El histórico sigue teniendo dónde pedirse."""
        from sync.sources import _cpe

        with patch("sunat_cpe.services.CpePortalClient"), \
                patch("sunat_cpe.services.CpeSynchronizer") as sincronizador:
            _cpe(Credentials(ruc=RUC, username="u", password="c"), Cadence.MANUAL)

        sincronizador.return_value.backfill.assert_called_once()


class SinClaveSolTests(TenantAPITestCase):
    """Las fuentes que no usan la clave SOL corren sin ella.

    Consultar el RUC de un proveedor es una consulta pública, y AFPnet tiene su
    propia sesión. Aun así, relanzar una de esas fuentes en una empresa que
    todavía no ha conectado SUNAT contestaba «Sin credenciales» y daba el
    trabajo por fallido: el botón «Revisar ahora» de Proveedores no servía justo
    en la empresa recién registrada, que es la que más falta le hace.
    """

    RUC = RUC

    def setUp(self):
        self.job = SyncJob.objects.create(
            organization=self.organization,
            kind=JobKind.MANUAL,
            steps=initial_steps(JobKind.MANUAL),
            status=JobStatus.DONE,
        )

    def _correr(self, key: str) -> dict:
        from sync.services import execute_step

        espia = replace(SOURCES_BY_KEY[key], run=lambda _c, _cad: {"revisados": 2})
        with patch.dict(SOURCES_BY_KEY, {key: espia}):
            job = execute_step(self.job, key, Cadence.NEW)
        return next(p for p in job.steps if p["key"] == key)

    def test_proveedores_corre_sin_sunat_conectada(self):
        self.assertFalse(hasattr(self.organization, "sunat_credential"))

        paso = self._correr("suppliers")

        self.assertEqual(paso["status"], StepStatus.DONE)
        self.assertEqual(paso["detail"], "revisados: 2")

    def test_una_fuente_con_clave_sigue_pidiendola(self):
        paso = self._correr("mailbox")

        self.assertEqual(paso["status"], StepStatus.SKIPPED)
        self.assertEqual(paso["detail"], "Sin credenciales")
