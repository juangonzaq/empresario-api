"""El tope por IP del calendario es para visitantes, no para la aplicación.

La vista nació como lead magnet abierto y limita a veinte consultas por hora y
por IP. Desde que el calendario de la aplicación la consume, ese mismo tope lo
gasta un usuario cambiando de régimen o marcando la planilla —y una oficina
entera comparte una sola IP de salida—, así que quien llega con sesión no
cuenta contra él.
"""

from __future__ import annotations

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from sensor_sunat.views import THROTTLE_MAX

RUC = "20604442533"


class CalendarioThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.url = reverse("sensor_sunat:calendario")

    def _get(self, **extra):
        return self.client.get(self.url, {"ruc": RUC}, **extra)

    def _bearer(self) -> dict:
        user = User.objects.create_user(
            email="dueno@empresa.pe", password="clave-de-pruebas-99",
            email_verified_at=timezone.now(),
        )
        token = RefreshToken.for_user(user).access_token
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def test_un_visitante_anonimo_sigue_topado(self):
        for _ in range(THROTTLE_MAX):
            self.assertEqual(self._get().status_code, 200)
        self.assertEqual(self._get().status_code, 429)

    def test_con_sesion_no_cuenta_contra_el_tope(self):
        cabeceras = self._bearer()
        for _ in range(THROTTLE_MAX + 5):
            self.assertEqual(self._get(**cabeceras).status_code, 200)

    def test_un_token_inventado_no_abre_la_puerta(self):
        cabeceras = {"HTTP_AUTHORIZATION": "Bearer no-es-un-token"}
        for _ in range(THROTTLE_MAX):
            self.assertEqual(self._get(**cabeceras).status_code, 200)
        self.assertEqual(self._get(**cabeceras).status_code, 429)

    def test_devuelve_los_eventos_del_grupo_del_ruc(self):
        data = self._get().json()
        self.assertEqual(data["ruc"], RUC)
        self.assertEqual(data["grupo"], "2-3")
        self.assertTrue(data["eventos"])
        # Es el contrato que consume el calendario del frontend.
        for evento in data["eventos"]:
            self.assertEqual(
                set(evento),
                {"fecha", "titulo", "tipo", "descripcion", "alarmas_dias", "recurrencia"},
            )
