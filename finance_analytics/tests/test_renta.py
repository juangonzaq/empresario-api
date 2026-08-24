"""Estimador de renta: escala RMT, pagos a cuenta y endpoint por régimen."""

from __future__ import annotations

from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from core.testing import TenantAPITestCase
from finance_analytics.services import renta
from sunat_cpe.models import Direction, DocumentClass, ElectronicInvoice

RUC = "20604442533"


def doc(period, amount, direction=Direction.ISSUED, klass=DocumentClass.INVOICE, n=[0]):
    n[0] += 1
    return ElectronicInvoice.objects.create(
        account_ruc=RUC, direction=direction, document_class=klass, document_type="10",
        issuer_ruc=RUC if direction == Direction.ISSUED else "20100000001",
        series="E001", number=str(n[0]), full_number=f"E001-{n[0]}", period=period,
        currency="PEN", total_amount=Decimal(amount), receiver_ruc="20100000001", receiver_name="CLIENTE",
    )


class EscalaTests(TenantAPITestCase):
    def test_rmt_escalonado_10_y_29_5(self):
        uit = Decimal("5500")
        r = renta.impuesto_anual("RMT", Decimal("100000"), uit)   # 100 000 > 82 500 (15 UIT)
        self.assertEqual(r["tramos"][0]["base"], Decimal("82500.00"))
        self.assertEqual(r["tramos"][0]["impuesto"], Decimal("8250.00"))
        self.assertEqual(r["tramos"][1]["base"], Decimal("17500.00"))
        self.assertEqual(r["tramos"][1]["impuesto"], Decimal("5162.50"))
        self.assertEqual(r["impuesto"], Decimal("13412.50"))
        self.assertAlmostEqual(r["tasa_efectiva"], 13.41, places=1)

    def test_rmt_dentro_del_primer_tramo_solo_10(self):
        r = renta.impuesto_anual("RMT", Decimal("50000"), Decimal("5500"))
        self.assertEqual(r["impuesto"], Decimal("5000.00"))
        self.assertEqual(r["tramos"][1]["base"], Decimal("0.00"))

    def test_perdida_no_paga(self):
        r = renta.impuesto_anual("RMT", Decimal("-3000"), Decimal("5500"))
        self.assertEqual(r["impuesto"], Decimal("0.00")); self.assertEqual(r["tasa_efectiva"], 0.0)

    def test_rg_plano(self):
        self.assertEqual(renta.impuesto_anual("RG", Decimal("100000"), Decimal("5500"))["impuesto"], Decimal("29500.00"))

    def test_pagos_a_cuenta(self):
        uit = Decimal("5500")
        self.assertEqual(renta.pago_a_cuenta("RMT", Decimal("10000"), Decimal("120000"), uit), (Decimal("0.01"), Decimal("100.00")))
        self.assertEqual(renta.pago_a_cuenta("RMT", Decimal("10000"), uit * 301, uit)[0], Decimal("0.015"))
        self.assertEqual(renta.pago_a_cuenta("RG", Decimal("10000"), Decimal("0"), uit)[1], Decimal("150.00"))
        self.assertEqual(renta.pago_a_cuenta("RER", Decimal("10000"), Decimal("0"), uit)[1], Decimal("150.00"))

    def test_rus(self):
        self.assertEqual(renta.cuota_rus(Decimal("4000"), Decimal("1000")), Decimal("20"))
        self.assertEqual(renta.cuota_rus(Decimal("7000"), Decimal("1000")), Decimal("50"))
        self.assertIsNone(renta.cuota_rus(Decimal("9000"), Decimal("0")))


class RentaEndpointTests(TenantAPITestCase):
    RUC = RUC

    def test_sin_regimen_asume_rmt_y_lo_dice(self):
        year = timezone.localdate().year
        p = f"{year}01"
        doc(p, "11800")                                  # venta 10 000 + IGV
        doc(p, "2360", direction=Direction.RECEIVED)     # compra 2 000 + IGV
        doc(p, "1180", klass=DocumentClass.CREDIT_NOTE)  # NC −1 000
        r = self.client.get(reverse("finance_analytics:renta"), {"year": year})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data["regime_assumed"]); self.assertEqual(r.data["regime"], "RMT")
        ene = r.data["months"][0]
        self.assertEqual(Decimal(str(ene["ingresos"])), Decimal("9000.00"))
        self.assertEqual(Decimal(str(ene["gastos"])), Decimal("2000.00"))
        self.assertEqual(Decimal(str(ene["utilidad"])), Decimal("7000.00"))
        self.assertEqual(Decimal(str(ene["pago_a_cuenta"])), Decimal("90.00"))
        self.assertEqual(Decimal(str(r.data["totals"]["utilidad"])), Decimal("7000.00"))
        self.assertIn("annual", r.data); self.assertEqual(len(r.data["annual"]["acumulado"]["tramos"]), 2)
        self.assertEqual(len(r.data["months"]), 12)

    def test_rer_no_tiene_anual(self):
        self.organization.tax_regime = "RER"; self.organization.save()
        r = self.client.get(reverse("finance_analytics:renta"))
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.data["annual"]); self.assertFalse(r.data["regime_assumed"])

    def test_anio_invalido(self):
        self.assertEqual(self.client.get(reverse("finance_analytics:renta"), {"year": "x"}).status_code, 400)


class RegimenQueNoCuadraTests(TenantAPITestCase):
    RUC = RUC

    def test_rus_con_ventas_grandes_se_estima_como_rmt_y_avisa(self):
        self.organization.tax_regime = "RUS"; self.organization.save()
        year = timezone.localdate().year
        for m in range(1, 4):
            doc(f"{year}{m:02d}", "118000")   # 100 000 netos al mes
        r = self.client.get(reverse("finance_analytics:renta"), {"year": year})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["regime_declared"], "RUS")
        self.assertEqual(r.data["regime"], "RMT")
        self.assertIn("tope", r.data["regime_mismatch"])
        self.assertIsNotNone(r.data["annual"])
        self.assertGreater(Decimal(str(r.data["annual"]["acumulado"]["impuesto"])), 0)

    def test_rus_dentro_de_sus_topes_sigue_siendo_rus(self):
        self.organization.tax_regime = "RUS"; self.organization.save()
        year = timezone.localdate().year
        doc(f"{year}01", "4720")   # 4 000 netos
        r = self.client.get(reverse("finance_analytics:renta"), {"year": year})
        self.assertEqual(r.data["regime"], "RUS"); self.assertIsNone(r.data["regime_mismatch"])
        self.assertEqual(Decimal(str(r.data["months"][0]["pago_a_cuenta"])), Decimal("20"))


class GastosPlanillaTests(TenantAPITestCase):
    RUC = RUC

    def _payroll(self, year, month, earning, employer, status="approved"):
        from payroll.models import (
            ConceptKind, PayrollConcept, PayrollEntry, PayrollEntryLine, PayrollPeriod,
        )
        from colaboradores.models import Colaborador
        per, _ = PayrollPeriod.objects.get_or_create(
            taxpayer_id=self.RUC, year=year, month=month,
            defaults={"status": status, "tax_unit_amount": Decimal("5500"), "minimum_wage_amount": Decimal("1130")},
        )
        col, _ = Colaborador.objects.get_or_create(
            taxpayer_id=self.RUC, document_number=f"D{month}", defaults={"full_name": "TRABAJADOR", "document_type": "DNI"},
        )
        entry = PayrollEntry.objects.create(period=per, colaborador=col, base_salary=Decimal(earning))
        ce = PayrollConcept.objects.get_or_create(taxpayer_id=self.RUC, code="H01", defaults={"name": "Haber", "kind": ConceptKind.EARNING})[0]
        cc = PayrollConcept.objects.get_or_create(taxpayer_id=self.RUC, code="E01", defaults={"name": "EsSalud", "kind": ConceptKind.EMPLOYER_COST})[0]
        cd = PayrollConcept.objects.get_or_create(taxpayer_id=self.RUC, code="D01", defaults={"name": "AFP", "kind": ConceptKind.DEDUCTION})[0]
        PayrollEntryLine.objects.create(entry=entry, concept=ce, amount=Decimal(earning))
        PayrollEntryLine.objects.create(entry=entry, concept=cc, amount=Decimal(employer))
        PayrollEntryLine.objects.create(entry=entry, concept=cd, amount=Decimal("1000"))  # descuento: NO cuenta

    def test_planilla_entra_como_gasto_y_solo_haberes_mas_aportes(self):
        year = timezone.localdate().year
        doc(f"{year}01", "118000")   # venta 100 000 netos
        self._payroll(year, 1, "10000", "900")  # costo empresa = 10 900 (el descuento de 1 000 no)
        r = self.client.get(reverse("finance_analytics:renta"), {"year": year})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["includes_payroll"])
        self.assertEqual(Decimal(str(r.data["breakdown"]["expenses"]["planilla"])), Decimal("10900.00"))
        ene = r.data["months"][0]
        self.assertEqual(Decimal(str(ene["components"]["gastos_planilla"])), Decimal("10900.00"))
        self.assertEqual(Decimal(str(ene["utilidad"])), Decimal("89100.00"))   # 100 000 − 10 900

    def test_ano_cerrado_no_se_proyecta_usa_actuals(self):
        # Un año pasado está cerrado: la proyección son los actuals, sin ×12.
        year = timezone.localdate().year - 1
        doc(f"{year}01", "118000")
        self._payroll(year, 1, "10000", "900")
        r = self.client.get(reverse("finance_analytics:renta"), {"year": year})
        proy = r.data["proyeccion"]
        self.assertEqual(Decimal(str(proy["planilla"])), Decimal("10900.00"))  # solo lo real
        self.assertEqual(r.data["assumptions"]["remaining_months"], 0)
        self.assertFalse(r.data["assumptions"]["editable"])

    def test_sin_planilla_lo_dice(self):
        year = timezone.localdate().year
        doc(f"{year}01", "118000")
        r = self.client.get(reverse("finance_analytics:renta"), {"year": year})
        self.assertFalse(r.data["includes_payroll"])
        self.assertTrue(any("planilla" in n.lower() for n in r.data["notes"]))


class ProjectionEditAndCloseTests(TenantAPITestCase):
    RUC = RUC

    def test_override_changes_only_the_remaining_projection(self):
        year = timezone.localdate().year
        for m in range(1, 4):
            doc(f"{year}{m:02d}", "118000")   # 100 000 netos/mes, 3 meses
        base = self.client.get(reverse("finance_analytics:renta"), {"year": year}).data
        auto_sales = Decimal(str(base["assumptions"]["sales"]["auto"]))
        self.assertGreater(base["assumptions"]["remaining_months"], 0)
        # Override ventas/mes a 0 → la proyección de los meses restantes no suma ventas
        r = self.client.put(reverse("finance_analytics:renta-assumptions"),
                            {"year": year, "monthly_sales": "0"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Decimal(str(r.data["assumptions"]["sales"]["used"])), Decimal("0"))
        # los actuals no cambian
        self.assertEqual(Decimal(str(r.data["totals"]["ingresos"])), Decimal("300000.00"))
        self.assertEqual(Decimal(str(r.data["proyeccion"]["ingresos"])), Decimal("300000.00"))  # 300k + 0*rem
        self.assertNotEqual(auto_sales, 0)

    def test_reset_override_back_to_auto(self):
        year = timezone.localdate().year
        doc(f"{year}01", "118000")
        self.client.put(reverse("finance_analytics:renta-assumptions"), {"year": year, "monthly_sales": "5000"}, format="json")
        r = self.client.put(reverse("finance_analytics:renta-assumptions"), {"year": year, "monthly_sales": ""}, format="json")
        self.assertIsNone(r.data["assumptions"]["sales"]["override"])

    def test_close_and_reopen_month(self):
        year = timezone.localdate().year
        doc(f"{year}01", "118000")
        c = self.client.post(reverse("finance_analytics:renta-close"), {"period": f"{year}01"}, format="json")
        self.assertEqual(c.status_code, 201)
        data = self.client.get(reverse("finance_analytics:renta"), {"year": year}).data
        self.assertIn(f"{year}01", data["closed_periods"])
        self.assertTrue(data["months"][0]["closed"])
        d = self.client.delete(reverse("finance_analytics:renta-close") + f"?period={year}01")
        self.assertEqual(d.status_code, 204)
        self.assertNotIn(f"{year}01", self.client.get(reverse("finance_analytics:renta"), {"year": year}).data["closed_periods"])

    def test_viewer_cannot_edit_or_close(self):
        from accounts.models import Membership, Role
        from accounts.tests.test_tenancy import make_user
        v = make_user("v@uno.pe"); Membership.objects.create(user=v, organization=self.organization, role=Role.VIEWER)
        self.client.force_authenticate(v)
        self.assertEqual(self.client.put(reverse("finance_analytics:renta-assumptions"), {"year": 2026}, format="json").status_code, 403)
        self.assertEqual(self.client.post(reverse("finance_analytics:renta-close"), {"period": "202601"}, format="json").status_code, 403)

    def test_bad_override_rejected(self):
        r = self.client.put(reverse("finance_analytics:renta-assumptions"), {"monthly_sales": "-5"}, format="json")
        self.assertEqual(r.status_code, 400)
