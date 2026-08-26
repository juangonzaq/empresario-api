"""El admin de usuarios renderiza con sus inlines y su búsqueda.

No se prueba el HTML: se prueba que la configuración (inlines de otras apps,
búsqueda por empresa con distinct, columnas calculadas) no revienta al pintar
el listado ni el detalle, que es donde los errores de admin aparecen recién
en ejecución.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import (
    Membership, OneTimeToken, Organization, Role, TokenPurpose, User,
)


class UserAdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email="root@pattern.pe", password="una-clave-larga-99"
        )
        self.client.force_login(self.admin)

        self.user = User.objects.create_user(
            email="ana@empresa.pe", password="una-clave-larga-99",
            first_name="Ana", last_name="Quispe",
        )
        org = Organization.objects.create(ruc="20604442533", name="Empresa SAC")
        Membership.objects.create(
            user=self.user, organization=org, role=Role.OWNER
        )
        OneTimeToken.issue(self.user, TokenPurpose.EMAIL_VERIFICATION)

    def test_changelist_renders(self):
        response = self.client.get(reverse("admin:accounts_user_changelist"))
        self.assertEqual(response.status_code, 200)

    def test_change_page_renders_with_inlines(self):
        response = self.client.get(
            reverse("admin:accounts_user_change", args=[self.user.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Empresas (membresías)")
        self.assertContains(response, "20604442533")

    def test_search_by_company_ruc_does_not_duplicate(self):
        # Dos empresas → dos memberships; sin distinct el usuario saldría dos veces.
        otra = Organization.objects.create(ruc="20512345678", name="Otra SAC")
        Membership.objects.create(user=self.user, organization=otra, role=Role.OWNER)
        response = self.client.get(
            reverse("admin:accounts_user_changelist"), {"q": "empresa"}
        )
        self.assertEqual(response.status_code, 200)
        result_list = response.context["cl"].result_list
        self.assertEqual(
            [u.pk for u in result_list].count(self.user.pk), 1
        )

    def test_filter_by_verified(self):
        self.user.email_verified_at = timezone.now()
        self.user.save(update_fields=["email_verified_at"])
        response = self.client.get(
            reverse("admin:accounts_user_changelist"), {"verificado": "no"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            self.user.pk, [u.pk for u in response.context["cl"].result_list]
        )
