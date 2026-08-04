"""Tests for the read-only compliance profile API."""

from __future__ import annotations

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from compliance_profile.models import ComplianceRating, ComplianceVariable

from .samples import TAXPAYER_ID


def create_rating(period: int, rating: str = "D", **overrides) -> ComplianceRating:
    defaults = {
        "taxpayer_id": TAXPAYER_ID,
        "period": period,
        "rating": rating,
        "preliminary_category": rating,
        "loaded_at": timezone.now(),
    }
    defaults.update(overrides)
    return ComplianceRating.objects.create(**defaults)


class ComplianceRatingAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.old = create_rating(202503, rating="C")
        cls.current = create_rating(
            202602, rating="D", is_current=True,
            detail_payload={}, detail_fetched_at=timezone.now(),
        )
        ComplianceVariable.objects.create(
            rating=cls.current, variable_type="P", code="v0615",
            description="No ha efectuado el pago del íntegro del IGV",
            severity="Muy grave", record_count=2,
        )
        cls.list_url = reverse("compliance_profile:compliance-rating-list")
        cls.current_url = reverse("compliance_profile:compliance-rating-current")

    def test_list_is_ordered_by_period_desc(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        periods = [row["period"] for row in response.data["results"]]
        self.assertEqual(periods, [202602, 202503])

    def test_filter_by_rating(self):
        response = self.client.get(self.list_url, {"rating": "C"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["period"], 202503)

    def test_current_returns_the_vigente_quarter_with_variables(self):
        response = self.client.get(self.current_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["period"], 202602)
        self.assertEqual(
            response.data["rating_label"], "Nivel de cumplimiento bajo"
        )
        self.assertEqual(len(response.data["variables"]), 1)
        self.assertEqual(response.data["variables"][0]["severity"], "Muy grave")

    def test_current_404s_when_nothing_scraped(self):
        ComplianceRating.objects.all().delete()
        response = self.client.get(self.current_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_retrieve_includes_raw_payloads(self):
        response = self.client.get(
            reverse(
                "compliance_profile:compliance-rating-detail",
                args=[self.current.pk],
            )
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("header_payload", response.data)
        self.assertIn("detail_payload", response.data)
