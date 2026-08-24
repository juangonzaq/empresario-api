"""Asientos de empresa por cuenta e invitaciones de usuarios a una empresa.

Dos controles nuevos:

* Cuántas empresas puede administrar un titular (tope por plan + extras).
* Sumar personas a **una** empresa, que solo verán esa.
"""

from __future__ import annotations

from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.models import Invitation, InvitationStatus, Membership, Role
from accounts.services import team
from accounts.tests.test_tenancy import PASSWORD, make_org, make_user


class CompanySeatTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.ana = make_user("ana@cuenta.pe")
        self.client.force_authenticate(self.ana)
        self.url = reverse("accounts:organizations")

    def _create(self, ruc: str):
        return self.client.post(self.url, {"ruc": ruc, "name": f"E {ruc}"})

    def test_default_plan_allows_three_companies(self):
        for i in range(3):
            self.assertEqual(self._create(f"2010000000{i}").status_code, 201)
        self.assertEqual(self.ana.owned_organizations_count, 3)

    def test_fourth_company_is_blocked_until_extra_seat_is_granted(self):
        for i in range(3):
            self._create(f"2010000000{i}")
        blocked = self._create("20100000009")
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.data.get("code"), "limite_empresas")

        # El dueño del sistema le otorga un asiento extra desde el admin.
        self.ana.extra_company_seats = 1
        self.ana.save(update_fields=["extra_company_seats"])
        self.assertEqual(self._create("20100000009").status_code, 201)
        self.assertEqual(self.ana.owned_organizations_count, 4)

    def test_seat_summary_reflects_use_and_limit(self):
        self._create("20100000001")
        from billing.services import seat_summary

        s = seat_summary(self.ana)
        self.assertEqual((s["used"], s["included"], s["limit"]), (1, 3, 3))
        self.assertEqual(s["available"], 2)


class TeamMembershipTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user("owner@empresa.pe")
        self.org = make_org("20100000001", self.owner)
        self.client.force_authenticate(self.owner)
        self.url = reverse("accounts:team")

    def test_inviting_an_existing_user_grants_scoped_access(self):
        beto = make_user("beto@empresa.pe")
        res = self.client.post(self.url, {"email": "beto@empresa.pe", "role": "viewer"})
        self.assertEqual(res.status_code, 201)
        membership = Membership.objects.get(user=beto, organization=self.org)
        self.assertEqual((membership.role, membership.is_active), (Role.VIEWER, True))
        self.assertEqual(membership.invited_by, self.owner)
        # Beto solo tiene esa empresa.
        self.assertEqual(beto.memberships.filter(is_active=True).count(), 1)

    def test_inviting_an_unknown_email_creates_a_pending_invitation(self):
        res = self.client.post(self.url, {"email": "nuevo@correo.pe", "role": "accountant"})
        self.assertEqual(res.status_code, 201)
        invite = Invitation.objects.get(email="nuevo@correo.pe", organization=self.org)
        self.assertEqual(invite.status, InvitationStatus.PENDING)
        self.assertEqual(invite.role, Role.ACCOUNTANT)

    def test_pending_invitation_is_accepted_on_login(self):
        self.client.post(self.url, {"email": "tardio@correo.pe", "role": "viewer"})
        # Esa persona recién crea su cuenta e inicia sesión.
        make_user("tardio@correo.pe")
        self.client.force_authenticate(None)
        res = self.client.post(
            reverse("accounts:login"),
            {"email": "tardio@correo.pe", "password": PASSWORD},
        )
        self.assertEqual(res.status_code, 200)
        rucs = {o["ruc"] for o in res.data["organizations"]}
        self.assertIn("20100000001", rucs)
        self.assertEqual(
            Invitation.objects.get(email="tardio@correo.pe").status,
            InvitationStatus.ACCEPTED,
        )

    def test_viewer_cannot_invite(self):
        lector = make_user("lector@empresa.pe")
        Membership.objects.create(user=lector, organization=self.org, role=Role.VIEWER)
        self.client.force_authenticate(lector)
        res = self.client.post(self.url, {"email": "x@correo.pe", "role": "viewer"})
        self.assertEqual(res.status_code, 403)

    def test_cannot_remove_the_last_owner(self):
        own = Membership.objects.get(user=self.owner, organization=self.org)
        res = self.client.delete(
            reverse("accounts:team-member", args=[own.id])
        )
        self.assertEqual(res.status_code, 400)
        own.refresh_from_db()
        self.assertTrue(own.is_active)

    def test_owner_can_change_a_members_role(self):
        beto = make_user("beto@empresa.pe")
        m = team.attach_member(self.org, beto, Role.VIEWER, self.owner)
        res = self.client.patch(
            reverse("accounts:team-member", args=[m.id]), {"role": "accountant"}
        )
        self.assertEqual(res.status_code, 200)
        m.refresh_from_db()
        self.assertEqual(m.role, Role.ACCOUNTANT)

    def test_removed_member_loses_access(self):
        beto = make_user("beto@empresa.pe")
        m = team.attach_member(self.org, beto, Role.VIEWER, self.owner)
        res = self.client.delete(reverse("accounts:team-member", args=[m.id]))
        self.assertEqual(res.status_code, 204)
        m.refresh_from_db()
        self.assertFalse(m.is_active)
