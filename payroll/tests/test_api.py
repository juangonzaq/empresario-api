"""API tests: state machine, live recalculation, manual lines, payslips
and tenant isolation."""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.urls import reverse
from rest_framework import status as http

from colaboradores.models import Colaborador
from core.testing import TenantAPITestCase

from payroll.models import PayrollPeriod

RUC = "20604442533"


class PayrollApiTests(TenantAPITestCase):
    def setUp(self):
        self.person = Colaborador.objects.create(
            taxpayer_id=RUC, document_number="44444444",
            full_name="Juana María Pérez Gómez", regimen="onp",
            monthly_salary=Decimal("3000.00"),
            hired_on=datetime.date(2024, 1, 1),
        )

    def create_period(self, year=2026, month=8):
        return self.client.post(
            reverse("payroll:periods"), {"year": year, "month": month},
            format="json",
        )

    def test_create_period_seeds_the_staff(self):
        response = self.create_period()
        self.assertEqual(response.status_code, http.HTTP_201_CREATED)
        data = response.data
        self.assertEqual(data["status"], "draft")
        self.assertEqual(len(data["entries"]), 1)
        row = data["entries"][0]
        self.assertEqual(row["worked_days"], 30)
        self.assertEqual(Decimal(row["net_pay"]), Decimal("2610.00"))
        self.assertIn("totals", data)

    def test_duplicate_period_is_rejected(self):
        self.create_period()
        self.assertEqual(self.create_period().status_code, http.HTTP_400_BAD_REQUEST)

    def test_missing_master_data_blocks_with_409(self):
        response = self.create_period(year=2031, month=1)
        self.assertEqual(response.status_code, http.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "master_data_missing")

    def test_patch_attendance_recalculates_live(self):
        period = self.create_period().data
        entry_id = period["entries"][0]["id"]
        response = self.client.patch(
            reverse("payroll:entry", args=[entry_id]),
            {"worked_days": 26, "absence_days": 4},
            format="json",
        )
        self.assertEqual(response.status_code, http.HTTP_200_OK)
        self.assertEqual(Decimal(response.data["gross_pay"]), Decimal("2200.00"))
        codes = [line["code"] for line in response.data["lines"]]
        self.assertIn("ABSENCE_DISCOUNT", codes)

    def test_manual_lines_enter_the_math(self):
        period = self.create_period().data
        entry_id = period["entries"][0]["id"]
        response = self.client.put(
            reverse("payroll:entry-manual-lines", args=[entry_id]),
            {"lines": [
                {"code": "BONUS", "amount": "500"},
                {"code": "SALARY_ADVANCE", "amount": "200"},
            ]},
            format="json",
        )
        self.assertEqual(response.status_code, http.HTTP_200_OK)
        # Bonus is pensionable: base 3500, ONP 455; advance comes off the net.
        self.assertEqual(Decimal(response.data["pension_base"]), Decimal("3500.00"))
        self.assertEqual(Decimal(response.data["total_deductions"]), Decimal("200.00"))
        self.assertEqual(Decimal(response.data["net_pay"]), Decimal("2845.00"))

    def test_manual_lines_reject_computed_and_unknown_concepts(self):
        period = self.create_period().data
        entry_id = period["entries"][0]["id"]
        url = reverse("payroll:entry-manual-lines", args=[entry_id])
        computed = self.client.put(
            url, {"lines": [{"code": "WORKED_SALARY", "amount": "10"}]},
            format="json",
        )
        self.assertEqual(computed.status_code, http.HTTP_400_BAD_REQUEST)
        unknown = self.client.put(
            url, {"lines": [{"code": "INVENTED", "amount": "10"}]}, format="json"
        )
        self.assertEqual(unknown.status_code, http.HTTP_400_BAD_REQUEST)

    def test_full_state_flow_and_closed_immutability(self):
        period_id = self.create_period().data["id"]
        calculate = reverse("payroll:calculate", args=[period_id])
        self.assertEqual(self.client.post(calculate).data["status"], "calculated")
        self.assertEqual(
            self.client.post(reverse("payroll:approve", args=[period_id])).data["status"],
            "approved",
        )
        close = self.client.post(
            reverse("payroll:close", args=[period_id]),
            {"accept_warnings": True}, format="json",
        )
        self.assertEqual(close.data["status"], "closed")

        # V15: a closed period accepts no writes, not even a recalculation.
        self.assertEqual(self.client.post(calculate).status_code, http.HTTP_409_CONFLICT)
        entry_id = close.data["entries"][0]["id"]
        patched = self.client.patch(
            reverse("payroll:entry", args=[entry_id]), {"worked_days": 1},
            format="json",
        )
        self.assertEqual(patched.status_code, http.HTTP_409_CONFLICT)
        deleted = self.client.delete(reverse("payroll:period", args=[period_id]))
        self.assertEqual(deleted.status_code, http.HTTP_409_CONFLICT)

    def test_close_requires_error_free_period(self):
        # AFP without commission type is a V2 error: approval is refused.
        self.person.regimen = "afp"
        self.person.afp = "integra"
        self.person.pension_commission_type = ""
        self.person.save()
        period_id = self.create_period().data["id"]
        self.client.post(reverse("payroll:calculate", args=[period_id]))
        approve = self.client.post(reverse("payroll:approve", args=[period_id]))
        self.assertEqual(approve.status_code, http.HTTP_409_CONFLICT)

    def test_payslip_downloads_as_pdf(self):
        period = self.create_period().data
        entry_id = period["entries"][0]["id"]
        response = self.client.get(reverse("payroll:entry-payslip", args=[entry_id]))
        self.assertEqual(response.status_code, http.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("boleta_44444444_2026-08.pdf", response["Content-Disposition"])

    def test_period_zip_bundles_every_payslip(self):
        period_id = self.create_period().data["id"]
        response = self.client.get(
            reverse("payroll:period-payslips", args=[period_id])
        )
        self.assertEqual(response["Content-Type"], "application/zip")

    def test_other_company_sees_nothing(self):
        period_id = self.create_period().data["id"]
        user, _ = self.make_tenant("20111111111", "otra@empresa.pe")
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(reverse("payroll:periods")).data, [])
        detail = self.client.get(reverse("payroll:period", args=[period_id]))
        self.assertEqual(detail.status_code, http.HTTP_404_NOT_FOUND)

    def test_incidents_point_at_the_entry(self):
        Colaborador.objects.create(
            taxpayer_id=RUC, document_number="55555555",
            full_name="Sin Sueldo Todavía", monthly_salary=None,
        )
        data = self.create_period().data
        v5 = [i for i in data["incidents"] if i["code"] == "V5"]
        self.assertEqual(len(v5), 1)
        self.assertEqual(v5[0]["severity"], "error")
        self.assertEqual(v5[0]["colaborador_name"], "Sin Sueldo Todavía")
        self.assertIsNotNone(v5[0]["entry_id"])


class EmployeePayslipsTests(TenantAPITestCase):
    """Las boletas viven asociadas al colaborador, con filtro por mes."""

    def setUp(self):
        self.person = Colaborador.objects.create(
            taxpayer_id=RUC, document_number="44444444",
            full_name="Juana María Pérez Gómez", regimen="onp",
            monthly_salary=Decimal("3000.00"),
            hired_on=datetime.date(2024, 1, 1),
        )
        for month in (6, 7, 8):
            self.client.post(
                reverse("payroll:periods"), {"year": 2026, "month": month},
                format="json",
            )
        self.url = reverse("payroll:employee-payslips", args=[self.person.id])

    def test_lists_every_period_newest_first(self):
        data = self.client.get(self.url).data
        self.assertEqual(data["full_name"], "Juana María Pérez Gómez")
        self.assertEqual([p["month"] for p in data["payslips"]], [8, 7, 6])
        self.assertEqual(data["years"], [2026])
        self.assertEqual(Decimal(data["payslips"][0]["net_pay"]), Decimal("2610.00"))

    def test_filters_by_month_and_year(self):
        data = self.client.get(self.url, {"year": 2026, "month": 7}).data
        self.assertEqual(len(data["payslips"]), 1)
        self.assertEqual(data["payslips"][0]["label"], "julio 2026")

    def test_other_company_gets_404(self):
        user, _ = self.make_tenant("20111111111", "otra@empresa.pe")
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(self.url).status_code, http.HTTP_404_NOT_FOUND)
