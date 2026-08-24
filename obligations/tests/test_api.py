"""The compliance API: overview, list, detail, evidence, actions, isolation."""

from __future__ import annotations

from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.tests.test_tenancy import make_org, make_user
from obligations import enums
from obligations.models import CompanyObligation


class ComplianceApiTests(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@empresa.pe")
        self.org = make_org("20100000001", self.owner)
        self.org.tax_regime = "RMT"
        self.org.save(update_fields=["tax_regime"])
        self.client.force_authenticate(self.owner)

    def test_overview_returns_the_whole_screen(self):
        res = self.client.get(reverse("obligations:overview"))
        self.assertEqual(res.status_code, 200)
        self.assertIn("summary", res.data)
        self.assertIn("score", res.data["summary"])
        self.assertIn("domains", res.data)

    def test_list_returns_obligations_scoped_to_company(self):
        self.client.get(reverse("obligations:overview"))  # triggers first evaluation
        res = self.client.get(reverse("obligations:list"))
        self.assertEqual(res.status_code, 200)
        self.assertGreaterEqual(res.data["count"], 15)
        codes = {row["code"] for row in res.data["results"]}
        self.assertIn("tax-monthly-igv-renta", codes)

    def test_filter_by_domain(self):
        self.client.get(reverse("obligations:overview"))
        res = self.client.get(reverse("obligations:list"), {"domain": "LABOR"})
        self.assertTrue(all(r["domain"] == "LABOR" for r in res.data["results"]))
        self.assertGreater(res.data["count"], 0)

    def test_completing_via_patch_marks_compliant(self):
        self.client.get(reverse("obligations:overview"))
        ob = CompanyObligation.objects.get(account_ruc=self.org.ruc, rule__code="muni-license")
        res = self.client.patch(
            reverse("obligations:detail", args=[ob.id]),
            {"workflow_status": enums.WorkflowStatus.COMPLETED}, format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["compliance_status"], enums.ComplianceStatus.COMPLIANT)

    def test_adding_evidence_confirms_the_obligation(self):
        self.client.get(reverse("obligations:overview"))
        ob = CompanyObligation.objects.get(account_ruc=self.org.ruc, rule__code="muni-itse")
        res = self.client.post(
            reverse("obligations:evidence", args=[ob.id]),
            {"evidence_type": "certificate", "label": "Certificado ITSE 2026"}, format="json",
        )
        self.assertEqual(res.status_code, 201)
        ob.refresh_from_db()
        self.assertEqual(ob.compliance_status, enums.ComplianceStatus.COMPLIANT)

    def test_create_and_update_action(self):
        self.client.get(reverse("obligations:overview"))
        ob = CompanyObligation.objects.get(account_ruc=self.org.ruc, rule__code="labor-plame")
        created = self.client.post(
            reverse("obligations:actions", args=[ob.id]),
            {"title": "Presentar PLAME", "priority": "high"}, format="json",
        )
        self.assertEqual(created.status_code, 201)
        action_id = created.data["id"]
        done = self.client.patch(
            reverse("obligations:action-detail", args=[action_id]),
            {"status": "done"}, format="json",
        )
        self.assertEqual(done.status_code, 200)
        self.assertEqual(done.data["status"], "done")
        self.assertIsNotNone(done.data["completed_at"])

    def test_recalculate(self):
        res = self.client.post(reverse("obligations:recalculate"))
        self.assertEqual(res.status_code, 200)
        self.assertIn("rules_evaluated", res.data)

    def test_a_company_cannot_see_anothers_obligation(self):
        self.client.get(reverse("obligations:overview"))
        mine = CompanyObligation.objects.filter(account_ruc=self.org.ruc).first()

        intruder = make_user("intruder@otra.pe")
        make_org("20100000009", intruder)
        self.client.force_authenticate(intruder)
        res = self.client.get(reverse("obligations:detail", args=[mine.id]))
        self.assertEqual(res.status_code, 404)
