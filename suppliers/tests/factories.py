"""Fixtures mimicking SUNAT's public RUC lookup responses."""

from __future__ import annotations

from suppliers.models import Supplier

# Real RUCs with valid check digits, used across the tests.
RUC_ACTIVE = "20100070970"
RUC_OTHER = "20604442533"

RESULT_PAGE = """
<html><body>
<div class="panel">
  <table>
    <tr><td>RUC:</td><td>{ruc} - {name}</td></tr>
    <tr><td>Tipo Contribuyente:</td><td>SOCIEDAD ANONIMA</td></tr>
    <tr><td>Nombre Comercial:</td><td>{trade}</td></tr>
    <tr><td>Fecha de Inscripción:</td><td>09/10/1992</td></tr>
    <tr><td>Estado:</td><td>{status}</td></tr>
    <tr><td>Condición:</td><td>{condition}</td></tr>
    <tr><td>Domicilio Fiscal:</td><td>CAL.MORELLI NRO. 181 - SAN BORJA</td></tr>
    <tr><td>Padrones :</td><td>NINGUNO
      <!-- developer comment SUNAT leaves in the markup -->
      <table>
        <tr><td>RUC:</td><td>{ruc} - {name}</td></tr>
        <tr><td>Fecha Inicio de Actividades:</td><td>01/06/1979</td></tr>
        <tr><td>Estado:</td><td>{status}</td></tr>
      </table>
    </td></tr>
  </table>
</div>
</body></html>
"""

EMPTY_PAGE = "<html><body><h1>CONSULTA RUC</h1></body></html>"
INVALID_PAGE = (
    "<html><body><p>El número de RUC 12345678901 consultado no es válido. "
    "Debe verificar el número.</p></body></html>"
)


def result_page(
    ruc: str = RUC_ACTIVE,
    name: str = "SUPERMERCADOS PERUANOS SOCIEDAD ANONIMA 'O ' S.P.S.A.",
    trade: str = "SUPERMERCADOS PERUANOS",
    status: str = "ACTIVO",
    condition: str = "HABIDO",
) -> str:
    return RESULT_PAGE.format(
        ruc=ruc, name=name, trade=trade, status=status, condition=condition
    )


def create_supplier(**overrides) -> Supplier:
    # Toda ficha de proveedor pertenece a una empresa; por defecto, la del
    # tenant que usan los tests (``core.testing.DEFAULT_RUC``).
    from core.testing import DEFAULT_RUC

    defaults = {
        "account_ruc": DEFAULT_RUC, "ruc": RUC_ACTIVE, "alias": "Supermercados",
    }
    return Supplier.objects.create(**{**defaults, **overrides})
