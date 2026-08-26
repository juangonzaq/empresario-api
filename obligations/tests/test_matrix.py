"""Ternary applicability — the core rule taken from the responsibility matrix:
missing data must yield «por determinar» plus the pending question, never a
silent «no te aplica». Concluding from silence is how compliance screens lie.
"""

from __future__ import annotations

from django.test import TestCase
from django.utils import timezone

from accounts.models import BusinessProfile
from accounts.tests.test_tenancy import make_org, make_user
from obligations import enums
from obligations.models import CompanyObligation
from obligations.services.applicability import evaluate_applicability, missing_facts_question
from obligations.services.context import CompanyContext, build_context
from obligations.services.engine import evaluate_company


def _ctx(**facts) -> CompanyContext:
    return CompanyContext(account_ruc="20100000001", today=timezone.localdate(), flat=facts)


class TernaryLogicTests(TestCase):
    """The Kleene table: an explicit fact settles the verdict even with gaps."""

    def test_unknown_fact_is_unknown_not_false(self):
        result = evaluate_applicability(
            {"all": [{"field": "company.has_premises", "operator": "truthy"}]},
            _ctx(**{"company.has_premises": None}),
        )
        self.assertIsNone(result.value)
        self.assertEqual(result.missing, ["company.has_premises"])

    def test_explicit_false_beats_unknown_in_all(self):
        # false AND unknown = false: se puede afirmar el «no» sin el otro dato.
        result = evaluate_applicability(
            {"all": [
                {"field": "company.sells_to_consumers", "operator": "truthy"},
                {"field": "company.has_premises", "operator": "truthy"},
            ]},
            _ctx(**{"company.sells_to_consumers": False, "company.has_premises": None}),
        )
        self.assertIs(result.value, False)
        self.assertEqual(result.missing, [])

    def test_explicit_true_beats_unknown_in_any(self):
        result = evaluate_applicability(
            {"any": [
                {"field": "company.has_payroll", "operator": "truthy"},
                {"field": "company.sells_to_consumers", "operator": "truthy"},
            ]},
            _ctx(**{"company.has_payroll": True, "company.sells_to_consumers": None}),
        )
        self.assertIs(result.value, True)

    def test_threshold_with_unknown_count_is_unknown(self):
        rule = {"all": [{"field": "company.worker_count", "operator": "gt", "value": 100}]}
        self.assertIsNone(evaluate_applicability(rule, _ctx(**{"company.worker_count": None})).value)
        self.assertIs(evaluate_applicability(rule, _ctx(**{"company.worker_count": 100})).value, False)
        self.assertIs(evaluate_applicability(rule, _ctx(**{"company.worker_count": 101})).value, True)

    def test_exists_answers_even_for_none(self):
        result = evaluate_applicability(
            {"all": [{"field": "company.sector", "operator": "exists"}]},
            _ctx(**{"company.sector": None}),
        )
        self.assertIs(result.value, False)

    def test_malformed_operator_fails_closed_without_questions(self):
        result = evaluate_applicability(
            {"all": [{"field": "company.sector", "operator": "regex", "value": ".*"}]},
            _ctx(**{"company.sector": None}),
        )
        self.assertIs(result.value, False)
        self.assertEqual(result.missing, [])

    def test_missing_facts_become_a_human_question(self):
        text = missing_facts_question(["company.sells_to_consumers"])
        self.assertIn("si vendes al consumidor final", text)


class EngineUnknownTests(TestCase):
    """End to end: a fresh company with no data gets questions, not verdicts."""

    def setUp(self):
        self.user = make_user("matriz@empresa.pe")
        self.org = make_org("20100000009", self.user)

    def _ob(self, code: str) -> CompanyObligation:
        return CompanyObligation.objects.get(account_ruc=self.org.ruc, rule__code=code)

    def test_fresh_company_has_undetermined_not_false_negatives(self):
        evaluate_company(self.org)
        # Sin planilla, sin ficha y sin perfil: nadie afirmó que no hay
        # trabajadores, así que PLAME queda por determinar con su pregunta.
        plame = self._ob("labor-plame")
        self.assertEqual(plame.applicability_status, enums.ApplicabilityStatus.UNKNOWN)
        self.assertIn("Falta saber", plame.current_assessment)

        # El Libro de Reclamaciones depende de hechos aún no declarados.
        book = self._ob("consumer-libro-reclamaciones")
        self.assertEqual(book.applicability_status, enums.ApplicabilityStatus.UNKNOWN)

    def test_declared_profile_settles_the_questions(self):
        BusinessProfile.objects.create(
            organization=self.org, people_count=1,
            sells_to_consumers=False, has_premises=False, sells_online=False,
            completed_at=timezone.now(),
        )
        evaluate_company(self.org)
        # people_count=1 → trabaja sola/o: ahora sí es un «no aplica» probado.
        self.assertEqual(self._ob("labor-plame").applicability_status,
                         enums.ApplicabilityStatus.NOT_APPLICABLE)
        self.assertEqual(self._ob("consumer-libro-reclamaciones").applicability_status,
                         enums.ApplicabilityStatus.NOT_APPLICABLE)
        self.assertEqual(self._ob("muni-license").applicability_status,
                         enums.ApplicabilityStatus.NOT_APPLICABLE)

    def test_consumer_with_shop_gets_the_claims_book(self):
        BusinessProfile.objects.create(
            organization=self.org, people_count=3,
            sells_to_consumers=True, has_premises=True, sells_online=False,
            completed_at=timezone.now(),
        )
        evaluate_company(self.org)
        self.assertEqual(self._ob("consumer-libro-reclamaciones").applicability_status,
                         enums.ApplicabilityStatus.APPLICABLE)
        # Sin canal digital, el libro virtual no aplica (hecho explícito).
        self.assertEqual(self._ob("consumer-libro-virtual").applicability_status,
                         enums.ApplicabilityStatus.NOT_APPLICABLE)
        # Con 3 personas declaradas también se activan las laborales.
        self.assertEqual(self._ob("labor-gratificaciones").applicability_status,
                         enums.ApplicabilityStatus.APPLICABLE)

    def test_corporate_rules_follow_the_ruc_prefix(self):
        evaluate_company(self.org)
        self.assertEqual(self._ob("corp-annual-shareholders").applicability_status,
                         enums.ApplicabilityStatus.APPLICABLE)

        natural = make_org("10456789012", make_user("natural@empresa.pe"))
        evaluate_company(natural)
        junta = CompanyObligation.objects.get(
            account_ruc=natural.ruc, rule__code="corp-annual-shareholders")
        self.assertEqual(junta.applicability_status, enums.ApplicabilityStatus.NOT_APPLICABLE)

    def test_overview_counts_the_pending_questions(self):
        from obligations.services.overview import build_overview

        overview = build_overview(self.org)
        self.assertGreater(overview["summary"]["undetermined"], 0)
