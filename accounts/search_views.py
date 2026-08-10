"""Búsqueda global sobre los datos de la empresa activa.

El buscador de la barra no hacía nada; el propio comentario del componente lo
admitía. Busca en lo que alguien tiene en la cabeza cuando escribe en esa caja:
el nombre o el RUC de un proveedor, el asunto de un mensaje de SUNAT, el número
de un comprobante.

Dos decisiones que condicionan el resto:

* **Todo va acotado a la empresa del request.** Se hereda de
  ``OrganizationAPIView`` y cada consulta filtra por su columna de dueño. Una
  búsqueda global es justo donde es fácil colar datos de otro sin darse cuenta.
* **Pocos resultados por tipo.** Esto alimenta un desplegable, no una pantalla
  de resultados: se devuelven los primeros de cada categoría y se dice cuántos
  hay en total, para que la interfaz pueda enlazar al listado completo.
"""

from __future__ import annotations

from django.db.models import Q
from rest_framework.request import Request
from rest_framework.response import Response

from .tenancy import OrganizationAPIView

POR_TIPO = 5
MINIMO = 2


def _proveedores(ruc: str, q: str) -> dict:
    from suppliers.models import Supplier

    encontrados = Supplier.objects.filter(account_ruc=ruc).filter(
        Q(ruc__icontains=q)
        | Q(alias__icontains=q)
        | Q(business_name__icontains=q)
        | Q(trade_name__icontains=q)
    )
    return {
        "total": encontrados.count(),
        "resultados": [
            {
                "id": str(s.id),
                "titulo": s.display_name,
                "detalle": f"RUC {s.ruc}",
                "aviso": s.has_issue,
            }
            for s in encontrados.order_by("-has_issue", "alias")[:POR_TIPO]
        ],
    }


def _mensajes(ruc: str, q: str) -> dict:
    from sunat_mailbox.models import Message

    encontrados = Message.objects.filter(taxpayer_id=ruc).filter(
        Q(subject__icontains=q) | Q(message_code__icontains=q)
    )
    return {
        "total": encontrados.count(),
        "resultados": [
            {
                "id": str(m.id),
                "titulo": m.subject,
                "detalle": m.sent_on.isoformat() if m.sent_on else "",
                "aviso": m.is_urgent and not m.is_reviewed,
            }
            for m in encontrados.order_by("-sent_on")[:POR_TIPO]
        ],
    }


def _comprobantes(ruc: str, q: str) -> dict:
    from sunat_cpe.models import ElectronicInvoice

    encontrados = ElectronicInvoice.objects.for_account(ruc).filter(
        Q(full_number__icontains=q)
        | Q(number__icontains=q)
        | Q(issuer_name__icontains=q)
        | Q(issuer_ruc__icontains=q)
    )
    return {
        "total": encontrados.count(),
        "resultados": [
            {
                "id": str(c.id),
                "titulo": c.full_number or f"{c.series}-{c.number}",
                "detalle": f"{c.issuer_name or c.issuer_ruc}".strip(),
                "aviso": False,
            }
            for c in encontrados.order_by("-issue_date")[:POR_TIPO]
        ],
    }


class GlobalSearchView(OrganizationAPIView):
    """``GET /api/search/?q=`` — proveedores, mensajes y comprobantes."""

    def get(self, request: Request) -> Response:
        q = (request.query_params.get("q") or "").strip()
        if len(q) < MINIMO:
            # Con una letra la consulta recorre todo y no acota nada; se
            # responde vacío en vez de castigar la base por cada tecla.
            return Response({"q": q, "grupos": []})

        grupos = [
            {"tipo": "proveedores", "etiqueta": "Proveedores",
             **_proveedores(request.ruc, q)},
            {"tipo": "mensajes", "etiqueta": "Buzón SUNAT",
             **_mensajes(request.ruc, q)},
            {"tipo": "comprobantes", "etiqueta": "Comprobantes",
             **_comprobantes(request.ruc, q)},
        ]
        return Response({
            "q": q,
            "grupos": [g for g in grupos if g["total"]],
        })
