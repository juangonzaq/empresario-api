"""API del registro de colaboradores.

* ``GET/POST /api/colaboradores/`` — la planilla y el alta a mano
* ``PATCH /api/colaboradores/{uuid}/`` — corregir la ficha o el sueldo
* ``DELETE /api/colaboradores/{uuid}/`` — quitar del registro
* ``POST /api/colaboradores/{uuid}/sueldo-afpnet/`` — volver a tomar el sueldo
  de la última remuneración declarada

Listar sincroniza antes con AFPnet. Es una escritura dentro de un GET, que no
es bonito, pero la alternativa —un botón de «actualizar»— deja la pantalla
mostrando gente que ya está enrolada y sueldos de hace dos meses hasta que
alguien se acuerde de pulsarlo.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from django.db import IntegrityError
from django.http import FileResponse
from django.utils import timezone
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.tenancy import CanManageOrganization, TenantScopedViewSetMixin

from .models import Colaborador, Contrato, Memorandum, OrigenSueldo
from .serializers import (
    ColaboradorSerializer, ContratoSerializer, MemorandumSerializer,
)
from .services import aplicar_sueldo_afpnet, sincronizar_desde_afpnet


class ColaboradorViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """El registro de trabajadores de la empresa activa."""

    tenant_field = "taxpayer_id"
    # Leer lo puede cualquier miembro; tocar sueldos, solo titular o contador.
    permission_classes = [IsAuthenticated, CanManageOrganization]

    queryset = Colaborador.objects.all()
    serializer_class = ColaboradorSerializer
    # Sin paginar: es la planilla entera y se mira entera —para sumar la masa
    # salarial o buscar a alguien—, y una planilla no crece como un histórico.
    pagination_class = None
    filter_backends = (filters.SearchFilter, filters.OrderingFilter)
    search_fields = ("full_name", "document_number", "cuspp", "position")
    ordering_fields = ("full_name", "monthly_salary", "hired_on", "created_at")
    ordering = ("full_name",)

    def list(self, request: Request, *args, **kwargs) -> Response:
        sincronizar_desde_afpnet(request.ruc)
        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        # El dueño no se acepta del cliente: sale de la empresa del request.
        # La unicidad se comprueba en el serializer, pero entre esa comprobación
        # y el insert cabe otra petición: una carrera debe salir como 400 con su
        # motivo, no como un 500.
        try:
            serializer.save(
                taxpayer_id=self.request.ruc, **self._sello_de_sueldo(serializer)
            )
        except IntegrityError as exc:
            raise ValidationError(
                {"document_number": "Ya tienes un colaborador con ese documento."}
            ) from exc

    def perform_update(self, serializer):
        try:
            serializer.save(**self._sello_de_sueldo(serializer))
        except IntegrityError as exc:
            raise ValidationError(
                {"document_number": "Ya tienes un colaborador con ese documento."}
            ) from exc

    @staticmethod
    def _sello_de_sueldo(serializer) -> dict:
        """Marca como propio el sueldo que llega escrito.

        A partir de aquí la sincronización deja de tocarlo: es la única forma
        de que corregir un sueldo signifique algo más que esperar a que AFPnet
        lo vuelva a pisar.
        """
        if "monthly_salary" not in serializer.validated_data:
            return {}
        return {
            "salary_source": OrigenSueldo.MANUAL,
            "salary_period": "",
            "salary_updated_at": timezone.now(),
        }

    @action(detail=True, methods=["post"], url_path="sueldo-afpnet")
    def sueldo_afpnet(self, request: Request, pk=None) -> Response:
        """Devuelve el sueldo a lo último que se declaró en AFPnet.

        Es el camino de vuelta de una corrección a mano. Lee lo ya guardado:
        para traer meses nuevos del portal está el detalle del afiliado, que es
        donde vive la sesión con CAPTCHA.
        """
        colaborador = self.get_object()
        if not colaborador.en_afpnet:
            return Response(
                {"detail": "Este colaborador no está afiliado a ninguna AFP.",
                 "code": "sin_afpnet"},
                status=status.HTTP_409_CONFLICT,
            )
        if not aplicar_sueldo_afpnet(colaborador):
            return Response(
                {"detail": "AFPnet todavía no tiene ninguna remuneración "
                           "declarada suya. Trae su historial y vuelve a "
                           "intentarlo.",
                 "code": "sin_historial"},
                status=status.HTTP_409_CONFLICT,
            )
        colaborador.save(update_fields=[
            "monthly_salary", "salary_source", "salary_period",
            "salary_updated_at", "updated_at",
        ])
        return Response(self.get_serializer(colaborador).data)


class MemorandumViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """Memorándums y comunicaciones internas por colaborador.

    * ``GET /api/memorandums/?colaborador={uuid}`` — los de una persona
    * ``POST /api/memorandums/`` — emitir uno (el número se genera si falta)
    * ``PATCH/DELETE /api/memorandums/{uuid}/`` — corregir o retirar

    Es el control que antes vivía en Excel; el documento firmado no se sube
    aquí, ``archivo`` guarda su ruta o enlace.
    """

    tenant_field = "taxpayer_id"
    permission_classes = [IsAuthenticated, CanManageOrganization]

    queryset = Memorandum.objects.select_related("colaborador")
    serializer_class = MemorandumSerializer
    # Sin paginar por la misma razón que la planilla: se mira el legajo entero
    # de una persona, y un legajo no crece como un histórico de comprobantes.
    pagination_class = None
    filter_backends = (filters.SearchFilter, filters.OrderingFilter)
    search_fields = ("numero", "asunto", "descripcion", "colaborador__full_name")
    ordering_fields = ("fecha_emision", "numero", "created_at")
    ordering = ("-fecha_emision", "-created_at")

    def get_queryset(self):
        queryset = super().get_queryset()
        colaborador = self.request.query_params.get("colaborador")
        if colaborador:
            try:
                uuid.UUID(colaborador)
            except ValueError:
                # Un identificador malformado no es un error del servidor:
                # simplemente no corresponde a nadie.
                return queryset.none()
            queryset = queryset.filter(colaborador_id=colaborador)
        return queryset

    def perform_create(self, serializer):
        numero = serializer.validated_data.get("numero") or Memorandum.siguiente_numero(
            self.request.ruc, serializer.validated_data["fecha_emision"].year
        )
        # La carrera entre generar el número y guardarlo debe salir como 400
        # con su motivo, no como un 500.
        try:
            serializer.save(taxpayer_id=self.request.ruc, numero=numero)
        except IntegrityError as exc:
            raise ValidationError(
                {"numero": "Ya existe un memorándum con ese número."}
            ) from exc

    def perform_update(self, serializer):
        try:
            serializer.save()
        except IntegrityError as exc:
            raise ValidationError(
                {"numero": "Ya existe un memorándum con ese número."}
            ) from exc


# Qué se acepta como contrato escaneado y hasta qué tamaño. La lista corta es
# a propósito: esto guarda contratos, no un adjuntador genérico.
EXTENSIONES_CONTRATO = {".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png"}
TAMANO_MAXIMO_CONTRATO = 10 * 1024 * 1024  # 10 MB


class ContratoViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """Contratos por colaborador, con su archivo firmado.

    * ``GET /api/contratos/?colaborador={uuid}`` — los de una persona
    * ``POST /api/contratos/`` — registrar uno (datos; el archivo va aparte)
    * ``PATCH/DELETE /api/contratos/{uuid}/`` — corregir o retirar
    * ``GET/POST/DELETE /api/contratos/{uuid}/archivo/`` — bajar, subir o
      quitar el documento; nunca queda en una URL pública.
    """

    tenant_field = "taxpayer_id"
    permission_classes = [IsAuthenticated, CanManageOrganization]

    queryset = Contrato.objects.select_related("colaborador")
    serializer_class = ContratoSerializer
    pagination_class = None
    filter_backends = (filters.OrderingFilter,)
    ordering_fields = ("fecha_inicio", "fecha_fin", "created_at")
    ordering = ("-fecha_inicio", "-created_at")

    def get_queryset(self):
        queryset = super().get_queryset()
        colaborador = self.request.query_params.get("colaborador")
        if colaborador:
            try:
                uuid.UUID(colaborador)
            except ValueError:
                return queryset.none()
            queryset = queryset.filter(colaborador_id=colaborador)
        return queryset

    def perform_create(self, serializer):
        serializer.save(taxpayer_id=self.request.ruc)

    def perform_destroy(self, instance):
        # El archivo se va con el contrato; dejarlo huérfano en disco solo
        # acumula documentos personales que ya nadie puede pedir.
        if instance.archivo:
            instance.archivo.delete(save=False)
        instance.delete()

    @action(
        detail=True, methods=["get", "post", "delete"], url_path="archivo",
        parser_classes=[MultiPartParser],
    )
    def archivo(self, request: Request, pk=None) -> Response | FileResponse:
        contrato = self.get_object()

        if request.method == "GET":
            if not contrato.archivo:
                return Response(
                    {"detail": "Este contrato no tiene archivo cargado."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            nombre = contrato.archivo.name.rsplit("/", 1)[-1]
            return FileResponse(
                contrato.archivo.open("rb"), as_attachment=True, filename=nombre
            )

        if request.method == "DELETE":
            if contrato.archivo:
                contrato.archivo.delete(save=True)
            return Response(self.get_serializer(contrato).data)

        subido = request.FILES.get("archivo")
        if subido is None:
            return Response(
                {"detail": "Adjunta el archivo en el campo «archivo»."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        extension = Path(subido.name).suffix.lower()
        if extension not in EXTENSIONES_CONTRATO:
            return Response(
                {"detail": "Formato no admitido. Sube PDF, Word o una imagen."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if subido.size > TAMANO_MAXIMO_CONTRATO:
            return Response(
                {"detail": "El archivo supera los 10 MB."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Subir de nuevo reemplaza: el contrato vigente es uno, no una pila
        # de versiones, y el archivo anterior no debe quedar huérfano.
        if contrato.archivo:
            contrato.archivo.delete(save=False)
        contrato.archivo = subido
        contrato.save(update_fields=["archivo", "updated_at"])
        return Response(self.get_serializer(contrato).data)
