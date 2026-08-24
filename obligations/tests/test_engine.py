"""The compliance engine: applicability, evidence-based verdicts, scoring."""

from __future__ import annotations

from django.test import TestCase
from django.utils import timezone

from accounts.tests.test_tenancy import make_org, make_user
from obligations import enums
from obligations.models import CompanyObligation
from obligations.services.context import CompanyContext, build_context
from obligations.services.engine import evaluate_company
from obligations.services.evaluators import _last_closed_period, payroll_registration
from obligations.services.overview import build_overview


def _rmt_org(ruc="20100000001"):
    user = make_user(f"o{ruc}@empresa.pe")
    org = make_org(ruc, user)
    org.tax_regime = "RMT"
    org.save(update_fields=["tax_regime"])
    return org


class ApplicabilityTests(TestCase):
    def test_annual_income_applies_to_rmt_not_to_rus(self):
        org = _rmt_org()
        evaluate_company(org)
        annual = CompanyObligation.objects.get(account_ruc=org.ruc, rule__code="tax-annual-income")
        self.assertEqual(annual.applicability_status, enums.ApplicabilityStatus.APPLICABLE)

        org.tax_regime = "RUS"
        org.save(update_fields=["tax_regime"])
        evaluate_company(org)
        annual.refresh_from_db()
        self.assertEqual(annual.applicability_status, enums.ApplicabilityStatus.NOT_APPLICABLE)

    def test_every_active_rule_lands_as_an_obligation(self):
        org = _rmt_org()
        evaluate_company(org)
        # 15 reglas sembradas; todas deben existir para la empresa (aplique o no).
        self.assertGreaterEqual(CompanyObligation.objects.filter(account_ruc=org.ruc).count(), 15)


class EvaluatorTests(TestCase):
    def test_monthly_declaration_compliant_when_period_is_declared(self):
        org = _rmt_org()
        from reconciliation.models import DeclaredSummary

        DeclaredSummary.objects.create(
            account_ruc=org.ruc, period=_last_closed_period(timezone.localdate()),
        )
        evaluate_company(org)
        ob = CompanyObligation.objects.get(account_ruc=org.ruc, rule__code="tax-monthly-igv-renta")
        self.assertEqual(ob.compliance_status, enums.ComplianceStatus.COMPLIANT)
        self.assertEqual(ob.verification_status, enums.VerificationStatus.INFERRED)

    def test_monthly_declaration_flags_missing_period(self):
        org = _rmt_org("20100000002")
        from reconciliation.models import DeclaredSummary

        DeclaredSummary.objects.create(account_ruc=org.ruc, period="202001")  # otro periodo
        evaluate_company(org)
        ob = CompanyObligation.objects.get(account_ruc=org.ruc, rule__code="tax-monthly-igv-renta")
        self.assertEqual(ob.compliance_status, enums.ComplianceStatus.NON_COMPLIANT)

    def test_consistency_control_reads_the_reconciliation_score(self):
        org = _rmt_org()
        from reconciliation.models import ConsistencyScore

        ConsistencyScore.objects.create(account_ruc=org.ruc, period="202607", score=40)
        evaluate_company(org)
        ob = CompanyObligation.objects.get(account_ruc=org.ruc, rule__code="tax-consistency-control")
        self.assertEqual(ob.compliance_status, enums.ComplianceStatus.NON_COMPLIANT)

    def test_payroll_registration_counts_employees(self):
        ctx = CompanyContext(
            account_ruc="20100000001", today=timezone.localdate(),
            flat={"company.active_employee_count": 3}, active_employee_count=3,
        )
        verdict = payroll_registration(ctx, rule=None)
        self.assertIn("3", verdict.reason)


class ScoringTests(TestCase):
    def test_overview_excludes_not_applicable_from_score_and_returns_shape(self):
        org = _rmt_org()
        evaluate_company(org)
        overview = build_overview(org)
        summary = overview["summary"]
        self.assertIsInstance(summary["score"], int)
        self.assertLessEqual(summary["score"], 100)
        self.assertEqual(summary["calculation"]["method"], "WEIGHTED_COMPLIANCE")
        # not_applicable no entra en la base del score.
        self.assertGreaterEqual(summary["applicable"], 1)
        for key in ("status_distribution", "domains", "priority_items", "trend", "upcoming_deadlines"):
            self.assertIn(key, overview)

    def test_completing_workflow_makes_it_compliant(self):
        org = _rmt_org()
        evaluate_company(org)
        ob = CompanyObligation.objects.filter(
            account_ruc=org.ruc, rule__code="corp-legal-books",
        ).first()
        ob.workflow_status = enums.WorkflowStatus.COMPLETED
        ob.save(update_fields=["workflow_status"])
        evaluate_company(org)
        ob.refresh_from_db()
        self.assertEqual(ob.compliance_status, enums.ComplianceStatus.COMPLIANT)
