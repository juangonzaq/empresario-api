"""Reconciliation API: tenant-scoped, dashboard payload, user classification."""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.test import override_settings
from django.urls import reverse

from core.testing import TenantAPITestCase
from reconciliation.models import BankMovement, MovementKind
from reconciliation.tests.test_engine import RUC, PERIOD, cpe, sire_sale


@override_settings(SUNAT={"RUC": RUC})
class ApiTests(TenantAPITestCase):
    RUC = RUC

    def test_run_then_summary(self):
        cpe("800", "11800.00"); sire_sale("800", "11800.00")
        cpe("801", "2360.00")
        r = self.client.post(reverse("reconciliation:run"), {"period": PERIOD}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        s = self.client.get(reverse("reconciliation:summary"), {"period": PERIOD})
        self.assertEqual(s.status_code, 200)
        self.assertEqual(s.data["run"]["period"], PERIOD)
        self.assertIn("score", s.data); self.assertLessEqual(s.data["score"]["value"], 100)
        self.assertTrue(any(a["title"].startswith("CPE") for a in s.data["alerts"]))
        docs = self.client.get(reverse("reconciliation:documents"), {"period": PERIOD, "only": "issues"})
        self.assertEqual(docs.status_code, 200)
        self.assertTrue(any(d["doc_key"].endswith("-801") for d in docs.data))

    def test_movements_create_and_user_classification_wins(self):
        r = self.client.post(reverse("reconciliation:movements"), {
            "date": "2026-07-15", "kind": "credit", "amount": "1500.00",
            "description": "DEPOSITO VENTANILLA", "bank": "BCP",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        pk = BankMovement.objects.get().pk
        c = self.client.post(reverse("reconciliation:movement-classify", args=[pk]), {"category": "loan"}, format="json")
        self.assertEqual(c.status_code, 200)
        m = BankMovement.objects.get()
        self.assertEqual(m.category, "loan"); self.assertEqual(m.classified_by, "user")
        self.assertEqual(m.period, "202607")

    def test_other_company_sees_nothing(self):
        BankMovement.objects.create(account_ruc=RUC, date=datetime.date(2026, 7, 1), period=PERIOD,
                                    kind=MovementKind.CREDIT, amount=Decimal("99"))
        from accounts.tests.test_tenancy import make_org, make_user
        beto = make_user("beto@dos.pe"); make_org("20200000002", beto)
        self.client.force_authenticate(beto)
        r = self.client.get(reverse("reconciliation:movements"))
        self.assertEqual(r.status_code, 200); self.assertEqual(r.data, [])
        s = self.client.get(reverse("reconciliation:summary"))
        self.assertIsNone(s.data["run"])
