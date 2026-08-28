"""API for the supplier registry and their daily SUNAT checks."""

from __future__ import annotations

import logging

from django.db.models import Count, Max, Q
from django.http import HttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from accounts.tenancy import TenantScopedViewSetMixin
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from .filters import SupplierCheckFilter, SupplierFilter
from .models import Supplier, SupplierCheck
from .serializers import (
    AltaMasivaSerializer,
    AnalisisProveedorSerializer,
    FiscalizacionSerializer,
    CompraPorProveedorSerializer,
    FacturaEnRiesgoSerializer,
    ResumenRiesgoSerializer,
    SupplierCheckSerializer,
    SupplierDetailSerializer,
    SupplierSerializer,
)
from .services import (
    RucLookupClient,
    RucLookupError,
    SupplierMonitor,
    analizar_proveedor,
    compras_por_proveedor,
    comprobantes_en_riesgo,
    describir_comprobantes,
    detalle_ssco,
    fecha_padron,
    incorporar_desde_compras,
    proveedores_por_descubrir,
    resumen_riesgo,
    rucs_en_padron,
    simular_fiscalizacion,
)

logger = logging.getLogger(__name__)


class SupplierViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """Manage the supplier registry and read their SUNAT standing.

    * ``GET/POST /api/suppliers/`` — list and register (the alta verifies on SUNAT)
    * ``GET /api/suppliers/{uuid}/`` — supplier with its recent checks
    * ``GET /api/suppliers/{uuid}/checks/`` — full history for one supplier
    * ``POST /api/suppliers/{uuid}/check/`` — run a check right now
    * ``GET /api/suppliers/summary/`` — how many are healthy, flagged or stale
    * ``GET /api/suppliers/discover/`` — emisores a los que compras, sin registrar
    * ``POST /api/suppliers/discover/`` — incorpora varios de una vez
    * ``GET /api/suppliers/tax-credit-risk/`` — el IGV que pones en juego
    * ``GET /api/suppliers/{uuid}/senales/`` — patrones sospechosos de un proveedor
    * ``GET /api/suppliers/fiscalizacion/`` — simulación de una fiscalización
    * ``GET /api/suppliers/reporte/`` — el informe completo en PDF
    """

    tenant_field = "account_ruc"

    def perform_create(self, serializer):
        """Da de alta un proveedor **después** de preguntarle a SUNAT por él.

        El registro a ciegas es el que hace daño: se incorpora un proveedor,
        se le compra, y meses después aparece que estaba NO HABIDO desde antes
        — con el crédito fiscal de esas facturas en discusión. Así que se
        consulta en el momento del alta y, si SUNAT lo marca, se rechaza con el
        motivo. Quien quiera registrarlo igual (para vigilarlo, precisamente)
        reenvía con ``accept_risk``: la decisión se toma viendo el problema, no
        sin saberlo.

        Si SUNAT no responde, el alta sigue adelante con el error anotado. Dejar
        a alguien sin poder registrar a su proveedor porque el portal está caído
        sería peor que registrarlo sin verificar, y la vigilancia diaria lo
        recogerá igual.
        """
        from django.db import IntegrityError

        acepta_riesgo = serializer.validated_data.pop("accept_risk", False)
        ruc = serializer.validated_data["ruc"]

        # Primero el padrón SSCO, que es local y no admite discusión: un SSCO
        # puede figurar ACTIVO y HABIDO en la ficha RUC y aun así sus facturas
        # no valen. Se mira antes de salir a SUNAT para no ocultarlo detrás
        # de un estado que parece limpio.
        sujeto = rucs_en_padron([ruc]).get(ruc)
        if sujeto is not None and not acepta_riesgo:
            raise ValidationError({
                "ruc": (
                    "Este RUC figura en el padrón de Sujetos Sin Capacidad "
                    "Operativa de SUNAT. Sus facturas no dan crédito fiscal ni "
                    "gasto, sin prueba en contrario."
                ),
                "sunat": {
                    "business_name": sujeto.razon_social,
                    "status": "",
                    "condition": "",
                    "has_issue": True,
                },
                "ssco": detalle_ssco(sujeto),
                "code": "proveedor_con_observaciones",
            })

        perfil, error = self._consultar_sunat(ruc)

        if perfil is not None and perfil.has_issue and not acepta_riesgo:
            raise ValidationError({
                "ruc": (
                    f"SUNAT reporta este RUC como {perfil.status or 'sin estado'} / "
                    f"{perfil.condition or 'sin condición'}. Comprarle puede costarte "
                    f"el crédito fiscal de sus facturas."
                ),
                "sunat": {
                    "business_name": perfil.business_name,
                    "status": perfil.status,
                    "condition": perfil.condition,
                    "has_issue": True,
                },
                "ssco": None,
                "code": "proveedor_con_observaciones",
            })

        # El dueño no se acepta del cliente: sale de la empresa del request.
        # La unicidad (empresa, proveedor) se comprueba en el serializer, pero
        # se atrapa también aquí: entre la validación y el insert cabe otra
        # petición, y una carrera no debe salir como error 500.
        try:
            supplier = serializer.save(account_ruc=self.request.ruc)
        except IntegrityError as exc:
            raise ValidationError(
                {"ruc": "Ya tienes registrado un proveedor con ese RUC."}
            ) from exc

        # Se reaprovecha la consulta ya hecha en vez de pedirla otra vez: deja
        # la ficha completa y el primer punto del historial desde el minuto uno.
        if perfil is not None:
            SupplierMonitor().check(supplier, cache={ruc: perfil})
        elif error:
            supplier.last_error = error[:500]
            supplier.save(update_fields=["last_error", "updated_at"])

        # La respuesta del alta lleva el resultado del cruce: es el «check»
        # que el usuario espera ver justo después de registrar.
        self._con_exposicion([supplier])

    @staticmethod
    def _consultar_sunat(ruc: str):
        """(perfil, error). Ninguno de los dos corta el alta por sí solo."""
        try:
            return RucLookupClient().fetch(ruc), ""
        except RucLookupError as exc:
            logger.warning("No se pudo verificar el RUC %s en SUNAT: %s", ruc, exc)
            return None, str(exc)

    queryset = Supplier.objects.all()
    filter_backends = (
        DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter
    )
    filterset_class = SupplierFilter
    search_fields = ("ruc", "alias", "business_name", "trade_name")
    # `purchases_total` no es columna de esta tabla: el importe sale de CPE y se
    # ordena en `list`. Aquí solo van los criterios que resuelve la base.
    ordering_fields = ("alias", "business_name", "ruc", "last_checked_at", "has_issue")
    ordering = ("-has_issue", "alias")

    def get_serializer_class(self):
        if self.action == "retrieve":
            return SupplierDetailSerializer
        return SupplierSerializer

    def _con_exposicion(self, suppliers: list[Supplier]) -> list[Supplier]:
        """Cuelga de cada proveedor lo que se le ha comprado.

        Se resuelve en Python y no con una anotación SQL porque los importes
        salen de otra app (``sunat_cpe``) y hay que netear notas de crédito:
        una sola consulta agregada para toda la página sale más barata que un
        subquery correlacionado por fila.
        """
        compras = compras_por_proveedor(self.request.ruc)
        padron = rucs_en_padron(s.ruc for s in suppliers)
        observados = self._observados()
        padron_al = fecha_padron()
        for supplier in suppliers:
            supplier.en_ssco = supplier.ruc in padron
            supplier.ssco = detalle_ssco(padron.get(supplier.ruc))
            supplier.padron_ssco_al = padron_al
            analisis = observados.get(supplier.ruc)
            supplier.nivel_riesgo = analisis.nivel if analisis else "sin_senales"
            supplier.puntaje_riesgo = analisis.puntaje if analisis else 0
            supplier.senales = len(analisis.senales) if analisis else 0
            compra = compras.get(supplier.ruc)
            supplier.purchases_total = compra.total if compra else None
            supplier.purchases_count = compra.comprobantes if compra else 0
            supplier.last_purchase_on = compra.ultima_compra if compra else None
        return suppliers

    def _observados(self) -> dict:
        """Los emisores con señales de fiscalización, por RUC, una vez por request."""
        if not hasattr(self, "_observados_cache"):
            self._observados_cache = {
                a.ruc: a for a in simular_fiscalizacion(self.request.ruc).proveedores
            }
        return self._observados_cache

    def _cartera_al_dia(self, queryset):
        """Todo emisor que te factura tiene ficha, y la lista enseña los vigilados.

        La alta es solo un INSERT (SUNAT se consulta en la sincronización),
        así que hacerla al listar es barato y evita el concepto «por
        incorporar», que nadie entendía. Los que el usuario dejó de vigilar
        siguen existiendo —no se vuelven a dar de alta— pero salen de la
        lista salvo que se pidan con ``?is_tracked=false``.
        """
        incorporar_desde_compras(self.request.ruc)
        if "is_tracked" not in self.request.query_params:
            queryset = queryset.filter(is_tracked=True)
        return queryset

    def perform_destroy(self, instance: Supplier) -> None:
        """Dejar de vigilar, no borrar: si se borrara, el alta automática lo
        volvería a meter en la siguiente carga y su historial se perdería."""
        instance.is_tracked = False
        instance.save(update_fields=["is_tracked", "updated_at"])

    def list(self, request: Request, *args, **kwargs) -> Response:
        queryset = self._cartera_al_dia(self.filter_queryset(self.get_queryset()))
        # «Con señales» no es una columna: sale del cruce con los comprobantes.
        # Se filtra sobre el total, no sobre la página, como los demás filtros.
        if request.query_params.get("con_senales") in ("true", "1"):
            queryset = queryset.filter(ruc__in=self._observados().keys())
        page = self.paginate_queryset(queryset)
        registros = self._con_exposicion(list(page if page is not None else queryset))

        # El orden por dinero se aplica aquí porque el importe no vive en esta
        # tabla; si el cliente pidió otro criterio, se respeta el suyo.
        if not request.query_params.get("ordering"):
            registros.sort(
                key=lambda s: (s.has_issue, s.puntaje_riesgo, s.purchases_total or 0),
                reverse=True,
            )

        serializer = self.get_serializer(registros, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    def retrieve(self, request: Request, *args, **kwargs) -> Response:
        supplier = self.get_object()
        self._con_exposicion([supplier])
        return Response(self.get_serializer(supplier).data)

    @action(detail=True, methods=["get"])
    def checks(self, request: Request, pk=None) -> Response:
        """The full check history for one supplier, paginated."""
        queryset = self.get_object().checks.all()
        page = self.paginate_queryset(queryset)
        serializer = SupplierCheckSerializer(page or queryset, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def check(self, request: Request, pk=None) -> Response:
        """Check this supplier on SUNAT immediately, without waiting for the cron."""
        supplier = self.get_object()
        try:
            result = SupplierMonitor().check(supplier)
        except RucLookupError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY
            )
        supplier.refresh_from_db()
        return Response({
            "supplier": SupplierSerializer(supplier).data,
            "check": SupplierCheckSerializer(result).data,
        })

    @action(detail=False, methods=["get"])
    def summary(self, request: Request) -> Response:
        queryset = self._cartera_al_dia(self.filter_queryset(self.get_queryset()))
        totals = queryset.aggregate(
            total=Count("id"),
            tracked=Count("id", filter=Q(is_tracked=True)),
            with_issues=Count("id", filter=Q(has_issue=True)),
            never_checked=Count("id", filter=Q(last_checked_at__isnull=True)),
            # Cuándo se validó la cartera por última vez: lo que el botón
            # «Validar en SUNAT» muestra para que se sepa si vale la pena pulsar.
            last_checked_at=Max("last_checked_at"),
        )
        by_status = {
            row["status"] or "unknown": row["count"]
            for row in queryset.order_by().values("status").annotate(count=Count("id"))
        }
        flagged = queryset.with_issues().values(
            "ruc", "alias", "business_name", "status", "condition"
        )
        return Response({**totals, "by_status": by_status, "flagged": list(flagged)})

    @action(detail=False, methods=["get", "post"], url_path="discover")
    def discover(self, request: Request) -> Response:
        """A quién le compras y todavía no vigilas — y el alta de golpe.

        Un registro que hay que llenar a mano se queda vacío, y un registro
        vacío no avisa de nada. Los comprobantes recibidos ya dicen con quién
        se trabaja de verdad, así que el alta se propone sola, ordenada por lo
        que cada proveedor pesa en la cuenta.
        """
        if request.method == "GET":
            pendientes = [
                {
                    "ruc": c.ruc,
                    "business_name": c.business_name,
                    "comprobantes": c.comprobantes,
                    "total": c.total,
                    "igv_estimado": c.igv_estimado,
                    "ultima_compra": c.ultima_compra,
                }
                for c in proveedores_por_descubrir(request.ruc)
            ]
            pagina = self.paginate_queryset(pendientes)
            serializer = CompraPorProveedorSerializer(
                pagina if pagina is not None else pendientes, many=True
            )
            if pagina is not None:
                return self.get_paginated_response(serializer.data)
            return Response(serializer.data)

        entrada = AltaMasivaSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        compras = compras_por_proveedor(request.ruc)
        ya_registrados = set(
            Supplier.objects.filter(account_ruc=request.ruc).values_list(
                "ruc", flat=True
            )
        )

        creados = [
            Supplier(
                account_ruc=request.ruc,
                ruc=ruc,
                business_name=getattr(compras.get(ruc), "business_name", ""),
                is_tracked=True,
            )
            for ruc in dict.fromkeys(entrada.validated_data["rucs"])
            if ruc not in ya_registrados and ruc != request.ruc
        ]
        Supplier.objects.bulk_create(creados, ignore_conflicts=True)

        # No se consulta a SUNAT aquí: doscientas altas serían doscientas
        # peticiones dentro de un request. Quedan sin consultar y los recoge la
        # sincronización (o el trabajo diario), que es donde eso pertenece.
        return Response(
            {"added": len(creados), "already_tracked": len(ya_registrados)},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["get"], url_path="tax-credit-risk")
    def tax_credit_risk(self, request: Request) -> Response:
        """Cuánto IGV pones en juego por comprarle a proveedores marcados.

        Es la pregunta que importa cuando llega una fiscalización, y la única
        forma de contestarla es cruzando la cartera con los comprobantes: el
        estado del proveedor sin el importe no dice si hay que preocuparse.

        Los totales van aparte del listado y se calculan sobre el conjunto
        entero: un importe que cambiara al pasar de página no serviría para
        decidir nada.
        """
        resumen = resumen_riesgo(request.ruc)
        comprobantes = comprobantes_en_riesgo(request.ruc)
        pagina = self.paginate_queryset(comprobantes)
        filas = describir_comprobantes(
            request.ruc, pagina if pagina is not None else comprobantes
        )
        listado = FacturaEnRiesgoSerializer(filas, many=True).data

        return Response({
            "totales": ResumenRiesgoSerializer(resumen).data,
            "facturas": (
                self.get_paginated_response(listado).data
                if pagina is not None
                else {"count": len(listado), "next": None,
                      "previous": None, "results": listado}
            ),
        })

    @action(detail=True, methods=["get"])
    def senales(self, request: Request, pk=None) -> Response:
        """Los patrones de facturación de este proveedor que un auditor miraría."""
        return Response(AnalisisProveedorSerializer(analizar_proveedor(self.get_object())).data)

    @action(detail=False, methods=["get"], url_path="reporte")
    def reporte(self, request: Request) -> HttpResponse:
        """El informe completo en PDF, para llevárselo al contador."""
        from .services.reporte import nombre_reporte, render_reporte

        pdf = render_reporte(request.organization)
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="{nombre_reporte(request.organization)}"'
        )
        return response

    @action(detail=False, methods=["get"])
    def fiscalizacion(self, request: Request) -> Response:
        """Simula una fiscalización por operaciones no reales.

        Cruza todas tus compras —de proveedores vigilados o no— con lo que se
        sabe de cada emisor y devuelve la contingencia: IGV y renta que se
        discutirían, más la multa. Es una estimación para dimensionar, y así
        se llama en pantalla.
        """
        return Response(FiscalizacionSerializer(simular_fiscalizacion(request.ruc)).data)


class SupplierCheckViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    tenant_field = "supplier__account_ruc"
    """Browse the daily check history across all suppliers."""

    queryset = SupplierCheck.objects.select_related("supplier")
    serializer_class = SupplierCheckSerializer
    filter_backends = (DjangoFilterBackend, filters.OrderingFilter)
    filterset_class = SupplierCheckFilter
    ordering_fields = ("checked_on", "status")
    ordering = ("-checked_on",)
