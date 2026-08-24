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
            "offering": "food", "sector": "food", "primary_goal": "tax_ready",
            "business_age": "starting", "people_count": 3,
        }, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["is_complete"])
        profile = BusinessProfile.objects.get(organization=self.org)
        self.assertEqual(profile.sector, "food")
        self.assertEqual(profile.people_count, 3)

    def test_food_rule_applies_only_to_food_business(self):
        # Sin perfil de comida: la regla de sanidad no aplica.
        evaluate_company(self.org)
        food = CompanyObligation.objects.get(account_ruc=self.org.ruc, rule__code="muni-food-sanitary")
        self.assertEqual(food.applicability_status, enums.ApplicabilityStatus.NOT_APPLICABLE)

        # Declaro que vendo comida → la regla pasa a aplicar.
        self.client.put(self.url, {"sector": "food"}, format="json")
        food.refresh_from_db()
        self.assertEqual(food.applicability_status, enums.ApplicabilityStatus.APPLICABLE)

    def test_people_count_signals_payroll(self):
        self.client.put(self.url, {"people_count": 4}, format="json")
        tregistro = CompanyObligation.objects.get(
            account_ruc=self.org.ruc, rule__code="labor-tregistro",
        )
        self.assertEqual(tregistro.applicability_status, enums.ApplicabilityStatus.APPLICABLE)
