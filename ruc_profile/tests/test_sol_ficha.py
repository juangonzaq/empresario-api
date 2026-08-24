"""Régimen de renta desde la Ficha RUC de SOL."""

import pathlib
from unittest.mock import patch

from django.utils import timezone

from core.testing import TenantAPITestCase
from ruc_profile.models import RucTaxAffectation
from ruc_profile.services import sol_ficha
from ruc_profile.services.sol_ficha import Tributo, detect_regime, parse_tributos

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "ficha_sol_tributos.html"


class ParseoTests(TenantAPITestCase):
    def test_lee_la_tabla_de_tributos_afectos(self):
        tributos = parse_tributos(FIXTURE.read_text(errors="ignore"))
        descs = [t.descripcion for t in tributos]
        self.assertIn("IGV - OPER. INT. - CTA. PROPIA", descs)
        self.assertIn("RENTA - REGIMEN MYPE TRIBUTARIO", descs)
        rmt = next(t for t in tributos if "MYPE" in t.descripcion)
        self.assertEqual(str(rmt.fecha_alta), "2019-04-15")
        self.assertEqual(detect_regime(tributos), "RMT")

    def test_mapeo_de_textos_a_regimen(self):
        T = lambda d: [Tributo(d, None, None)]  # noqa: E731
        self.assertEqual(detect_regime(T("RENTA - REGIMEN MYPE TRIBUTARIO")), "RMT")
        self.assertEqual(detect_regime(T("RENTA - REGIMEN ESPECIAL")), "RER")
        self.assertEqual(detect_regime(T("RENTA-3RA. CATEGOR. - CTA.PROPIA")), "RG")
        self.assertEqual(detect_regime(T("NUEVO RUS - CATEGORIA 1")), "RUS")
        # las retenciones de 4ta/5ta no son el régimen de la empresa
        self.assertIsNone(detect_regime(T("RENTA 4TA. CATEG. RETENCIONES") + T("IGV - OPER. INT. - CTA. PROPIA")))

    def test_sync_guarda_tributos_y_fija_el_regimen_desde_sunat(self):
        from accounts.models import SunatCredential
        cred = SunatCredential.objects.create(organization=self.organization, sol_username="X")
        self.organization.tax_regime = "RUS"; self.organization.tax_regime_source = "usuario"; self.organization.save()
        tribs = parse_tributos(FIXTURE.read_text(errors="ignore"))
        with patch.object(sol_ficha, "fetch_tributos", return_value=tribs):
            r = sol_ficha.sync_regime(self.organization, cred)
        self.assertEqual(r["regimen"], "RMT")
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.tax_regime, "RMT")          # SUNAT manda sobre lo declarado a mano
        self.assertEqual(self.organization.tax_regime_source, "sunat")
        self.assertIsNotNone(self.organization.tax_regime_checked_at)
        self.assertEqual(RucTaxAffectation.objects.filter(ruc=self.RUC).count(), len(tribs))
        # el perfil lo expone
        me = self.client.get("/api/me/").data["organizations"][0]
        self.assertEqual(me["tax_regime"], "RMT"); self.assertEqual(me["tax_regime_source"], "sunat")

    def test_el_sync_incluye_el_paso_y_va_con_clave_sol(self):
        from sync.sources import SOURCES_BY_KEY, initial_steps
        self.assertTrue(SOURCES_BY_KEY["tributos"].needs_sol)
        self.assertIn("tributos", [s["key"] for s in initial_steps("inicial")])
        self.assertIn("tributos", [s["key"] for s in initial_steps("mensual")])
        self.assertNotIn("tributos", [s["key"] for s in initial_steps("diaria")])
