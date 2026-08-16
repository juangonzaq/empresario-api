"""Límites de petición en los endpoints que se atacan primero.

Medido antes del cambio: 60 intentos de login seguidos, todos respondidos, a 10
por segundo y por proceso; y 30 correos de recuperación enviados sin freno.

Los tests corren contra los límites **reales configurados**, no contra unos
inventados para el test: lo que interesa comprobar es lo que se despliega. Si
alguien sube un límite sin pensarlo, estos tests se lo recuerdan.
"""

from __future__ import annotations

from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User

PASSWORD = "una-clave-larga-99"


def limite(scope: str) -> int:
    """Peticiones permitidas por ventana según la configuración vigente."""
    rate = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"][scope]
    return int(rate.split("/")[0])


# El hash real de contraseñas domina el tiempo de estos tests, y aquí no se
# está comprobando la fortaleza del hash.
@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class ThrottlingTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email="victima@empresa.pe", password=PASSWORD,
            email_verified_at=timezone.now(),
        )

    def _login(self, password="mal", **extra):
        return self.client.post(
            reverse("accounts:login"),
            {"email": "victima@empresa.pe", "password": password},
            format="json", **extra,
        ).status_code

    def test_brute_force_against_one_account_is_stopped(self):
        tope = limite("login_por_cuenta")
        codes = [self._login() for _ in range(tope + 2)]
        self.assertIn(429, codes, "el login aceptó intentos sin límite")
        # Y responde 400 hasta agotar el cupo, no antes.
        self.assertEqual(codes[0], 400)

    def test_the_limit_follows_the_account_not_just_the_ip(self):
        """Rotar de IP no sirve: el contador va también por cuenta atacada."""
        for _ in range(limite("login_por_cuenta")):
            self._login()
        desde_otra_ip = self._login(REMOTE_ADDR="203.0.113.99")
        self.assertEqual(desde_otra_ip, 429)

    def test_a_correct_password_still_works_below_the_limit(self):
        self.assertEqual(self._login(password=PASSWORD), 200)

    def test_password_reset_cannot_be_used_to_bomb_an_inbox(self):
        url = reverse("accounts:password-reset")
        codes = [
            self.client.post(url, {"email": "victima@empresa.pe"}, format="json").status_code
            for _ in range(limite("correo") + 2)
        ]
        self.assertIn(429, codes, "se pueden enviar correos sin freno")

    def test_registration_is_capped_per_ip(self):
        url = reverse("accounts:register")
        codes = [
            self.client.post(
                url, {"email": f"nuevo{i}@empresa.pe", "password": PASSWORD},
                format="json",
            ).status_code
            for i in range(limite("registro") + 2)
        ]
        self.assertIn(429, codes)

    def test_looking_at_the_sunat_connection_does_not_burn_the_quota(self):
        """Mirar el estado no es intentar conectar.

        El perfil y el onboarding piden este GET al abrirse. Cuando contaba
        para el límite de conexión, entrar unas cuantas veces a mirar dejaba al
        usuario una hora sin poder conectar —y el mensaje hablaba de demasiados
        intentos, que era exactamente lo que no había hecho.
        """
        from core.testing import TenantAPITestCase

        user, _ = TenantAPITestCase.make_tenant("20604442533", "titular@empresa.pe")
        self.client.force_authenticate(user)
        url = reverse("accounts:sunat-connection")

        codes = {
            self.client.get(url).status_code
            for _ in range(limite("sunat_conexion") + 3)
        }
        self.assertEqual(codes, {200})

    def test_the_shipped_limits_are_not_absurd(self):
        """Un cambio descuidado de configuración no debe pasar inadvertido."""
        self.assertLessEqual(limite("login_por_cuenta"), 20)
        self.assertLessEqual(limite("correo"), 10)
        self.assertLessEqual(limite("registro"), 20)
