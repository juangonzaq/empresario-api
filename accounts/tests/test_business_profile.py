"""El perfil del negocio y cómo mueve el mapa de obligaciones."""

from __future__ import annotations

from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.models import BusinessProfile
from accounts.tests.test_tenancy import make_org, make_user
from obligations import enums
from obligations.models import CompanyObligation
from obligations.services.engine import evaluate_company


class BusinessProfileApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user("owner@negocio.pe")
        self.org = make_org("20100000001", self.owner)
        self.org.tax_regime = "RMT"
        self.org.save(update_fields=["tax_regime"])
        self.client.force_authenticate(self.owner)
        self.url = reverse("accounts:business-profile")

    def test_put_saves_and_marks_complete(self):
        res = self.client.put(self.url, {
            "offering": "food", "sectors": ["food", "commerce"],
            "goals": ["tax_ready", "cashflow"],
            "business_age": "starting", "people_count": 3,
        }, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["is_complete"])
        self.assertEqual(res.data["sectors"], ["food", "commerce"])
        profile = BusinessProfile.objects.get(organization=self.org)
        self.assertEqual(profile.sectors, ["food", "commerce"])
        # El primero elegido es el principal (columna espejo para admin/reportes).
        self.assertEqual(profile.sector, "food")
        self.assertEqual(profile.primary_goal, "tax_ready")
        self.assertEqual(profile.people_count, 3)

    def test_sectors_capped_and_deduplicated(self):
        res = self.client.put(self.url, {
            "sectors": ["food", "commerce", "services", "construction"],
        }, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("sectors", res.data)

        res = self.client.put(self.url, {"sectors": ["food", "food"], "goals": ["growth", "growth"]},
                              format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["sectors"], ["food"])
        self.assertEqual(res.data["goals"], ["growth"])

        res = self.client.put(self.url, {"sectors": ["astrologia"]}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_food_rule_applies_only_to_food_business(self):
        # Sin perfil: no sabemos qué vende → «por determinar», nunca un no.
        evaluate_company(self.org)
        food = CompanyObligation.objects.get(account_ruc=self.org.ruc, rule__code="muni-food-sanitary")
        self.assertEqual(food.applicability_status, enums.ApplicabilityStatus.UNKNOWN)

        # Declara otro rubro y otra oferta: ahora sí es un no.
        self.client.put(self.url, {"sectors": ["services"], "offering": "services"}, format="json")
        food.refresh_from_db()
        self.assertEqual(food.applicability_status, enums.ApplicabilityStatus.NOT_APPLICABLE)

        # Declaro que vendo comida → la regla pasa a aplicar.
        self.client.put(self.url, {"sectors": ["food"]}, format="json")
        food.refresh_from_db()
        self.assertEqual(food.applicability_status, enums.ApplicabilityStatus.APPLICABLE)

    def test_several_sectors_combine_obligations(self):
        # Restaurante que además hace obra: sanidad de alimentos Y SENCICO.
        # Las reglas se suman, nunca se elige una sola.
        self.client.put(self.url, {"sectors": ["construction", "food"]}, format="json")
        statuses = dict(
            CompanyObligation.objects.filter(
                account_ruc=self.org.ruc, rule__code__in=["muni-food-sanitary", "tax-sencico"],
            ).values_list("rule__code", "applicability_status")
        )
        self.assertEqual(statuses["muni-food-sanitary"], enums.ApplicabilityStatus.APPLICABLE)
        self.assertEqual(statuses["tax-sencico"], enums.ApplicabilityStatus.APPLICABLE)

        # Si deja de hacer obra, SENCICO deja de aplicar.
        self.client.put(self.url, {"sectors": ["food"]}, format="json")
        sencico = CompanyObligation.objects.get(account_ruc=self.org.ruc, rule__code="tax-sencico")
        self.assertEqual(sencico.applicability_status, enums.ApplicabilityStatus.NOT_APPLICABLE)

    def test_people_count_signals_payroll(self):
        self.client.put(self.url, {"people_count": 4}, format="json")
        tregistro = CompanyObligation.objects.get(
            account_ruc=self.org.ruc, rule__code="labor-tregistro",
        )
        self.assertEqual(tregistro.applicability_status, enums.ApplicabilityStatus.APPLICABLE)


class PayrollQuestionTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user("owner2@negocio.pe")
        self.org = make_org("20100000002", self.owner)
        self.org.tax_regime = "RMT"
        self.org.save(update_fields=["tax_regime"])
        self.client.force_authenticate(self.owner)
        self.url = reverse("accounts:business-profile")

    def _session_payroll(self):
        from accounts.models import Organization
        from accounts.serializers import OrganizationSerializer

        # Instancia fresca: el perfil es un OneToOne y Django cachea su ausencia.
        return OrganizationSerializer(Organization.objects.get(pk=self.org.pk)).data["has_payroll"]

    def test_declared_payroll_reaches_the_session_and_the_obligations(self):
        self.assertIsNone(self._session_payroll())

        self.client.put(self.url, {"has_payroll": True}, format="json")
        self.assertIs(self._session_payroll(), True)
        tregistro = CompanyObligation.objects.get(account_ruc=self.org.ruc, rule__code="labor-tregistro")
        self.assertEqual(tregistro.applicability_status, enums.ApplicabilityStatus.APPLICABLE)

        self.client.put(self.url, {"has_payroll": False}, format="json")
        self.assertIs(self._session_payroll(), False)
        tregistro.refresh_from_db()
        self.assertEqual(tregistro.applicability_status, enums.ApplicabilityStatus.NOT_APPLICABLE)
