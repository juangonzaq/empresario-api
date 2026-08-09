"""Base para los tests de API después de la conversión a multiempresa.

Antes todos los endpoints eran públicos y un test podía llamarlos sin más.
Ahora exigen bearer y una empresa atribuible, así que cada caso necesita un
usuario con acceso al RUC cuyos datos siembra.

    class MisTests(TenantAPITestCase):
        RUC = "20604442533"

        def test_algo(self):
            self.client.get("/api/loquesea/")   # ya autenticado sobre self.org

Quien quiera comprobar el comportamiento sin sesión que llame a
``self.client.force_authenticate(None)`` explícitamente: que cerrar sesión sea
un gesto visible en el test, no el estado por defecto.
"""

from __future__ import annotations

from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APITestCase

DEFAULT_RUC = "20604442533"
DEFAULT_PASSWORD = "clave-de-pruebas-99"


class TenantAPITestCase(APITestCase):
    """Un usuario titular de una empresa, ya autenticado.

    El tenant se arma en ``_pre_setup``, no en ``setUp``: así una subclase que
    define su propio ``setUp`` sin llamar a ``super()`` —cosa habitual— sigue
    quedando autenticada, en vez de recibir 401 por todas partes.
    """

    RUC = DEFAULT_RUC
    EMAIL = "pruebas@empresa.pe"

    def _pre_setup(self):
        super()._pre_setup()
        # El caché sobrevive entre tests: sin esto, un test lee el panel que
        # dejó cacheado el anterior y falla por motivos que no son suyos.
        cache.clear()
        self.user, self.organization = self.make_tenant(self.RUC, self.EMAIL)
        self.client.force_authenticate(self.user)

    @staticmethod
    def make_tenant(ruc: str, email: str):
        """Crea (o reutiliza) usuario, empresa y membresía de titular."""
        from accounts.models import Membership, Organization, Role, User

        user, _ = User.objects.get_or_create(
            email=email,
            defaults={"email_verified_at": timezone.now()},
        )
        if not user.has_usable_password():
            user.set_password(DEFAULT_PASSWORD)
            user.save(update_fields=["password"])
        organization, _ = Organization.objects.get_or_create(
            ruc=ruc, defaults={"name": f"EMPRESA {ruc}"}
        )
        Membership.objects.get_or_create(
            user=user, organization=organization, defaults={"role": Role.OWNER}
        )
        return user, organization
