"""Cómo se decide, en cada request, de qué empresa son los datos.

Regla única: **la organización sale de las membresías de quien llama, nunca de
un parámetro del request**. Un cliente puede *elegir* entre las empresas a las
que ya tiene acceso (cabecera ``X-Organization`` o ``?organization=``), pero si
pide una que no le pertenece recibe 404, no los datos.

Uso desde una vista:

    class MiVista(OrganizationAPIView):
        def get(self, request):
            docs = load_documents(request.ruc)   # ya está acotado al tenant

``request.organization`` y ``request.ruc`` los deja ``resolve_organization``.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, PermissionDenied
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.views import APIView

from billing.permissions import SubscriptionActive

from .models import Membership, Organization

ORG_HEADER = "HTTP_X_ORGANIZATION"
ORG_PARAM = "organization"


class NoOrganization(APIException):
    """El usuario entró pero todavía no tiene ninguna empresa.

    El cuerpo lleva ``code`` además del texto: el frontend necesita distinguir
    «todavía no creaste tu empresa» —que lleva al onboarding— de cualquier otro
    error, y no debe hacerlo comparando mensajes.
    """

    status_code = status.HTTP_409_CONFLICT
    default_detail = "Aún no has registrado ninguna empresa."
    default_code = "sin_organizacion"

    def __init__(self, detail: str | None = None):
        super().__init__({
            "detail": detail or self.default_detail,
            "code": self.default_code,
        })


def user_memberships(user):
    return (
        Membership.objects.filter(user=user, is_active=True)
        .select_related("organization")
        .order_by("organization__name", "organization__ruc")
    )


def resolve_membership(request: Request) -> Membership:
    """La membresía activa del request.

    Si viene un identificador de organización se usa **solo para elegir entre
    las propias**; si no corresponde a ninguna, es 404. Sin identificador, y
    con una sola empresa, se toma esa; con varias, hay que decir cuál.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        raise PermissionDenied("Necesitas iniciar sesión.")

    memberships = list(user_memberships(user))
    if not memberships:
        raise NoOrganization()

    wanted = (
        request.META.get(ORG_HEADER)
        or request.query_params.get(ORG_PARAM)
        or ""
    ).strip()

    if wanted:
        for membership in memberships:
            org = membership.organization
            if wanted in (str(org.id), org.ruc):
                return membership
        # No se distingue "no existe" de "no es tuya": ambas son 404.
        raise NotFound("Empresa no encontrada.")

    if len(memberships) == 1:
        return memberships[0]
    raise NotFound(
        "Tienes acceso a varias empresas: indica cuál con la cabecera "
        "X-Organization."
    )


def resolve_organization(request: Request) -> Organization:
    return resolve_membership(request).organization


class HasOrganization(BasePermission):
    """Deja pasar solo si el request se puede atribuir a una empresa."""

    message = "Necesitas una empresa activa para esta operación."

    def has_permission(self, request: Request, view) -> bool:
        request.membership = resolve_membership(request)
        request.organization = request.membership.organization
        request.ruc = request.organization.ruc
        return True


class CanManageOrganization(HasOrganization):
    """Además, exige rol de titular o contador (no solo lectura)."""

    message = "Tu rol en esta empresa no permite esta acción."

    def has_permission(self, request: Request, view) -> bool:
        super().has_permission(request, view)
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True
        return request.membership.can_manage


class OrganizationAPIView(APIView):
    """Base de toda vista que lee o escribe datos de una empresa.

    Hereda de aquí y `request.ruc` ya viene acotado a quien llama. Si una
    vista de datos no hereda de esta clase, está sirviendo datos sin dueño:
    hay un test que recorre las rutas y lo detecta.

    También exige suscripción vigente (prueba o pagada): terminada la prueba,
    los datos responden 402 hasta que la empresa elija un plan. Lo que hace
    falta para pagar vive en ``billing`` y no pasa por aquí.
    """

    permission_classes = [IsAuthenticated, HasOrganization, SubscriptionActive]


class ManagedOrganizationAPIView(OrganizationAPIView):
    permission_classes = [IsAuthenticated, CanManageOrganization, SubscriptionActive]


class TenantScopedViewSetMixin:
    """Acota el queryset de un ViewSet a la empresa de quien llama.

    Se declara el nombre de la columna que guarda el RUC dueño del dato, que
    en este proyecto es ``taxpayer_id`` o ``account_ruc`` según la app:

        class MessageViewSet(TenantScopedViewSetMixin, ReadOnlyModelViewSet):
            tenant_field = "taxpayer_id"

    El filtro se aplica en ``get_queryset``, así que cubre también el detalle
    (``retrieve``): pedir por id un registro de otra empresa devuelve 404.
    """

    tenant_field = "taxpayer_id"
    permission_classes = [IsAuthenticated, HasOrganization, SubscriptionActive]

    def get_queryset(self):
        queryset = super().get_queryset()
        ruc = getattr(self.request, "ruc", None)
        if not ruc:
            # Sin empresa resuelta no se devuelve nada. Nunca "todo".
            return queryset.none()
        return queryset.filter(**{self.tenant_field: ruc})


def visible_rucs(request: Request) -> list[str]:
    """Los RUC sobre los que la empresa del request puede consultar datos
    públicos: el suyo y los de los proveedores que ella misma registró."""
    from suppliers.models import Supplier

    ruc = getattr(request, "ruc", None)
    if not ruc:
        return []
    suppliers = Supplier.objects.filter(account_ruc=ruc).values_list("ruc", flat=True)
    return [ruc, *suppliers]
