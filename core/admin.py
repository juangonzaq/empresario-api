"""Infraestructura compartida del admin: marca y filtro por empresa.

El dato del producto vive bajo tres convenciones de tenant que crecieron por
app: FK/O2O a ``accounts.Organization``, ``account_ruc`` y ``taxpayer_id``
(más algún ``ruc`` suelto). El filtro fábrica de aquí las unifica bajo un solo
parámetro — ``?empresa=<ruc>`` — igual en todos los changelists; de ese
contrato dependen los enlaces del panorama en la ficha de la empresa
(``accounts.admin.OrganizationAdmin``).
"""

from __future__ import annotations

from django.contrib import admin

admin.site.site_header = "EMPRESARIO · Administración"
admin.site.site_title = "EMPRESARIO"
admin.site.index_title = "Para ver todo lo de un cliente entra por Empresas (Cuentas)"


def filtro_empresa(campo: str):
    """Filtro «empresa» para un modelo tenant, sea cual sea su campo.

    ``campo`` es el lookup hacia el RUC de la empresa: ``account_ruc``,
    ``taxpayer_id``, ``ruc``, ``organization__ruc``, ``supplier__account_ruc``…
    El desplegable lista razón social + RUC, que es como se reconoce a un
    cliente; el RUC pelado del changelist no lo es.
    """

    class FiltroEmpresa(admin.SimpleListFilter):
        title = "empresa"
        parameter_name = "empresa"

        def lookups(self, request, model_admin):
            from accounts.models import Organization

            return [
                (o.ruc, f"{o.name} · {o.ruc}")
                for o in Organization.objects.order_by("name")
            ]

        def queryset(self, request, queryset):
            valor = self.value()
            if valor:
                return queryset.filter(**{campo: valor})
            return queryset

    return FiltroEmpresa
