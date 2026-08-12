"""Conectar AFPnet desde la interfaz, con el CAPTCHA de por medio.

Tres endpoints: ver cómo está la conexión, pedir una imagen y responderla. El
CAPTCHA lo resuelve el empresario en su pantalla —es un control anti-bots de
AFPnet y aquí no se automatiza—, así que la interfaz necesita justo esto.
"""

from __future__ import annotations

from django.db.models import Count, Sum
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.tenancy import ManagedOrganizationAPIView, OrganizationAPIView
from accounts.throttles import SunatConexionThrottle

from .models import (
    Afp, AfpnetAffiliate, AfpnetCompany, AfpnetDebt, AfpnetDeclaration,
    AfpnetPeriodSummary, AfpnetSession,
)
from .serializers import (
    AfpnetAffiliateSerializer, AfpnetCompanySerializer, AfpnetDebtSerializer,
    AfpnetDeclarationSerializer, AfpnetPeriodSummarySerializer,
)
from .services import client, relevo


def _estado(sesion: AfpnetSession | None) -> dict:
    if sesion is None:
        return {"estado": "sin_conectar", "usuario": "", "abierta_en": None,
                "ultimo_error": ""}
    return {
        "estado": "activa" if sesion.is_usable else sesion.status,
        "usuario": sesion.username,
        "abierta_en": sesion.opened_at,
        "ultimo_error": sesion.last_error,
    }


class AfpnetEstadoView(OrganizationAPIView):
    """Cómo está la conexión de esta empresa con AFPnet."""

    def get(self, request: Request) -> Response:
        sesion = AfpnetSession.objects.filter(
            organization=request.organization
        ).first()
        return Response(_estado(sesion))


class AfpnetDesafioView(ManagedOrganizationAPIView):
    """Pide a AFPnet una imagen de CAPTCHA para que la resuelva la persona.

    Lleva el mismo límite de peticiones que conectar SUNAT: cada llamada abre
    una visita al portal, y pedir imágenes en bucle es justo lo que un control
    anti-bots debe frenar —aunque lo hagamos sin querer.
    """

    throttle_classes = [SunatConexionThrottle]

    def post(self, request: Request) -> Response:
        try:
            handle, imagen = relevo.crear(request.organization)
        except client.PortalCerrado as exc:
            # 503 y no 502: no está roto, está cerrado, y volverá solo.
            return Response(
                {"detail": str(exc), "code": "portal_cerrado"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except client.AfpnetError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY
            )
        return Response({"handle": handle, "captcha": imagen})


class AfpnetConectarView(ManagedOrganizationAPIView):
    """Completa el login con lo que escribió la persona."""

    throttle_classes = [SunatConexionThrottle]

    def post(self, request: Request) -> Response:
        datos = request.data
        faltan = [
            campo for campo in ("handle", "usuario", "clave", "captcha")
            if not str(datos.get(campo, "")).strip()
        ]
        if faltan:
            return Response(
                {campo: ["Este campo es obligatorio."] for campo in faltan},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            sesion = relevo.resolver(
                request.organization,
                str(datos["handle"]).strip(),
                str(datos["usuario"]).strip(),
                str(datos["clave"]),
                str(datos["captcha"]).strip(),
            )
        except client.DesafioCaducado as exc:
            return Response(
                {"detail": str(exc), "code": "captcha_caducado"},
                status=status.HTTP_409_CONFLICT,
            )
        except client.LoginRechazado as exc:
            # Distinguirlos importa de verdad: con «captcha» la interfaz ofrece
            # otra imagen, mientras que insistir con una clave que AFPnet acaba
            # de rechazar puede bloquear el usuario de la empresa.
            return Response(
                {
                    "detail": str(exc),
                    "code": "captcha_incorrecto" if exc.captcha else "credenciales",
                    "reintentable": exc.captcha,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except client.AfpnetError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY
            )

        return Response(_estado(sesion), status=status.HTTP_201_CREATED)


class AfpnetDesconectarView(ManagedOrganizationAPIView):
    """Cierra la sesión guardada. No cierra la de AFPnet: borra la nuestra."""

    def post(self, request: Request) -> Response:
        sesion = AfpnetSession.objects.filter(
            organization=request.organization
        ).first()
        if sesion is not None:
            sesion.marcar_caducada("Desconectada desde la aplicación.")
        return Response(_estado(sesion))


class AfpnetDatosView(OrganizationAPIView):
    """Todo lo guardado de AFPnet para la empresa activa, en una respuesta.

    La pantalla lo enseña junto, así que pedirlo junto evita cuatro viajes y
    que el usuario vea las secciones aparecer a destiempo.
    """

    def get(self, request: Request) -> Response:
        ruc = request.ruc
        empresa = AfpnetCompany.objects.filter(organization=request.organization).first()
        planillas = AfpnetDeclaration.objects.filter(taxpayer_id=ruc)[:36]
        deudas = AfpnetDebt.objects.filter(taxpayer_id=ruc)
        afiliados = AfpnetAffiliate.objects.filter(taxpayer_id=ruc)
        periodos = AfpnetPeriodSummary.objects.filter(taxpayer_id=ruc)[:24]

        todas = AfpnetDeclaration.objects.filter(taxpayer_id=ruc)
        importes = todas.aggregate(
            fondo=Sum("nominal_fondo"), ryr=Sum("nominal_ryr")
        )

        # Desglose por administradora: una empresa reparte trabajadores entre
        # varias y el total agregado esconde cuál concentra el aporte.
        por_afp = [
            {
                "afp": fila["afp"],
                "afp_label": str(Afp(fila["afp"]).label),
                "planillas": fila["n"],
                "fondo": fila["fondo"],
            }
            for fila in todas.values("afp").annotate(
                n=Count("id"), fondo=Sum("nominal_fondo")
            ).order_by("-fondo")
        ]

        return Response({
            "empresa": AfpnetCompanySerializer(empresa).data if empresa else None,
            "planillas": AfpnetDeclarationSerializer(planillas, many=True).data,
            "deudas": AfpnetDebtSerializer(deudas, many=True).data,
            "afiliados": AfpnetAffiliateSerializer(afiliados, many=True).data,
            "periodos": AfpnetPeriodSummarySerializer(periodos, many=True).data,
            "por_afp": por_afp,
            "totales": {
                "planillas": todas.count(),
                "pagadas": todas.filter(is_paid=True).count(),
                "deudas": deudas.count(),
                "afiliados": afiliados.count(),
                "fondo": importes["fondo"],
                "ryr": importes["ryr"],
                "periodos": AfpnetPeriodSummary.objects.filter(
                    taxpayer_id=ruc
                ).count(),
            },
        })


class AfpnetEnrolarView(ManagedOrganizationAPIView):
    """Da de alta a un trabajador consultando su ficha en AFPnet.

    Le da de alta **en nuestra base**: la consulta contra AFPnet es de solo
    lectura y no registra a nadie allí.
    """

    def post(self, request: Request) -> Response:
        documento = str(request.data.get("documento", "")).strip()
        if not documento:
            return Response(
                {"documento": ["Este campo es obligatorio."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from .services.sync import SinSesion, enrolar_trabajador

        try:
            afiliado = enrolar_trabajador(request.organization, documento)
        except SinSesion as exc:
            return Response(
                {"detail": str(exc), "code": "sin_sesion"},
                status=status.HTTP_409_CONFLICT,
            )
        except client.PortalCerrado as exc:
            return Response({"detail": str(exc), "code": "portal_cerrado"},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except client.AfpnetError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        if afiliado is None:
            return Response(
                {"detail": "AFPnet no encontró a nadie con ese documento. "
                           "Puede que no esté afiliado al sistema privado.",
                 "code": "no_encontrado"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(AfpnetAffiliateSerializer(afiliado).data,
                        status=status.HTTP_201_CREATED)


class AfpnetAfiliadoDetalleView(OrganizationAPIView):
    """El historial mes a mes de un trabajador.

    ``?refrescar=1`` lo vuelve a pedir a AFPnet; sin eso sirve lo guardado, que
    es lo normal al abrir la ficha.
    """

    def get(self, request: Request, cuspp: str) -> Response:
        from .models import AfpnetContribution
        from .serializers import AfpnetContributionSerializer

        afiliado = AfpnetAffiliate.objects.filter(
            taxpayer_id=request.ruc, cuspp=cuspp
        ).first()
        if afiliado is None:
            return Response({"detail": "No encontrado."},
                            status=status.HTTP_404_NOT_FOUND)

        if request.query_params.get("refrescar") == "1":
            from .services.sync import SinSesion, historial_de

            try:
                historial_de(request.organization, cuspp)
            except SinSesion as exc:
                return Response({"detail": str(exc), "code": "sin_sesion"},
                                status=status.HTTP_409_CONFLICT)
            except client.AfpnetError as exc:
                return Response({"detail": str(exc)},
                                status=status.HTTP_502_BAD_GATEWAY)

        aportes = AfpnetContribution.objects.filter(affiliate=afiliado)
        deuda = sum(
            (a.declared_unpaid or 0) for a in aportes
        ) or None
        return Response({
            "afiliado": AfpnetAffiliateSerializer(afiliado).data,
            "aportes": AfpnetContributionSerializer(aportes, many=True).data,
            "totales": {
                "meses": aportes.count(),
                "aportado": sum((a.paid_fund or 0) for a in aportes) or None,
                "deuda": deuda,
            },
        })
