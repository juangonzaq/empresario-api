"""Manual income/expense records coded at capture time: the category
travels to the financial transaction immediately — no sync button — and
new categories extend the chart of accounts from the same form."""

from __future__ import annotations

from decimal import Decimal

from django.urls import reverse
from rest_framework import status as http

from core.testing import TenantAPITestCase

from financials.models import FinancialTransaction, TransactionCategory

RUC = "20604442533"


class ManualEntryCategorizationTests(TenantAPITestCase):
    def crear(self, **overrides):
        payload = {
            "direction": "recibida",
            "entry_date": "2026-08-05",
            "description": "Alquiler de oficina agosto",
            "amount": "1500.00",
            "category_code": "ADMIN_EXPENSES",
            **overrides,
        }
        return self.client.post(
            reverse("finance_analytics:manual-entries"), payload, format="json"
        )

    def transaccion(self, entry_id: str) -> FinancialTransaction | None:
        return FinancialTransaction.objects.filter(
            taxpayer_id=RUC, external_id=entry_id
        ).first()

    def test_capture_with_category_lands_confirmed_in_the_statement(self):
        response = self.crear()
        self.assertEqual(response.status_code, http.HTTP_201_CREATED)
        self.assertEqual(response.data["category_code"], "ADMIN_EXPENSES")

        row = self.transaccion(response.data["id"])
        self.assertIsNotNone(row)
        self.assertEqual(row.category.code, "ADMIN_EXPENSES")
        self.assertEqual(row.categorization_status, "confirmed")
        self.assertEqual(row.net_amount_pen, Decimal("1500.00"))

    def test_capture_without_category_waits_in_the_queue(self):
        response = self.crear(category_code="")
        row = self.transaccion(response.data["id"])
        self.assertIsNone(row.category)
        self.assertEqual(row.categorization_status, "uncategorized")

    def test_unknown_category_is_rejected(self):
        response = self.crear(category_code="NO_EXISTE")
        self.assertEqual(response.status_code, http.HTTP_400_BAD_REQUEST)

    def test_editing_the_category_updates_the_transaction(self):
        creado = self.crear().data
        response = self.client.patch(
            reverse("finance_analytics:manual-entry", args=[creado["id"]]),
            {"category_code": "SELLING_EXPENSES"},
            format="json",
        )
        self.assertEqual(response.status_code, http.HTTP_200_OK)
        row = self.transaccion(creado["id"])
        self.assertEqual(row.category.code, "SELLING_EXPENSES")

    def test_deleting_the_record_deletes_its_transaction(self):
        creado = self.crear().data
        self.client.delete(reverse("finance_analytics:manual-entry", args=[creado["id"]]))
        self.assertIsNone(self.transaccion(creado["id"]))

    def test_new_category_extends_the_chart_and_is_usable(self):
        response = self.client.post(
            reverse("financials:categories"),
            {
                "name": "Servicios de contabilidad",
                "statement_line": "ADMIN_EXPENSES_LINE",
                "applies_to": "purchases",
            },
            format="json",
        )
        self.assertEqual(response.status_code, http.HTTP_201_CREATED)
        code = response.data["code"]
        self.assertEqual(code, "SERVICIOS_DE_CONTABILIDAD")
        category = TransactionCategory.objects.get(taxpayer_id=RUC, code=code)
        self.assertEqual(category.statement_line.code, "ADMIN_EXPENSES_LINE")
        # Expense line: the sign follows its siblings (−1), so the income
        # statement subtracts it like every other expense category.
        self.assertEqual(category.sign, -1)

        creado = self.crear(category_code=code).data
        row = self.transaccion(creado["id"])
        self.assertEqual(row.category.code, code)
        self.assertEqual(row.categorization_status, "confirmed")

    def test_new_category_requires_a_statement_line(self):
        response = self.client.post(
            reverse("financials:categories"),
            {"name": "Suelta", "applies_to": "both"},
            format="json",
        )
        self.assertEqual(response.status_code, http.HTTP_400_BAD_REQUEST)
