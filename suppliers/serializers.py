"""Serializers for the suppliers API."""

from __future__ import annotations

from rest_framework import serializers

from .models import Supplier, SupplierCheck
from .validators import is_valid_ruc


class SupplierCheckSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupplierCheck
        fields = (
            "id", "checked_on", "status", "condition", "has_issue", "changed",
            "previous_status", "previous_condition", "succeeded", "error",
        )


class SupplierSerializer(serializers.ModelSerializer):
    """Suppliers are writable: this is the registry the user maintains."""

    display_name = serializers.CharField(read_only=True)

    # Cuánto se le ha comprado, según los comprobantes recibidos. Lo anota la
    # vista; sin él la lista no se puede ordenar por dinero en riesgo.
    purchases_total = serializers.DecimalField(
        max_digits=16, decimal_places=2, read_only=True, default=None,
    )
    purchases_count = serializers.IntegerField(read_only=True, default=0)
    last_purchase_on = serializers.DateField(read_only=True, default=None)

    # Registrar a sabiendas un proveedor que SUNAT marca. Sin esto la alta se
    # rechaza con el motivo, que es justo el aviso que evita el problema.
    accept_risk = serializers.BooleanField(write_only=True, required=False, default=False)

    def validate_ruc(self, value: str) -> str:
        """Un RUC no puede repetirse dentro de la misma empresa.

        Antes había dos ``validate_ruc`` en esta clase y la segunda pisaba a la
        primera, así que esta comprobación no llegaba a ejecutarse nunca: el
        duplicado solo lo frenaba la restricción de la base, y el usuario veía
        el error genérico en vez de este.
        """
        ruc = value.strip()
        account_ruc = getattr(self.context.get("request"), "ruc", None)
        existing = Supplier.objects.filter(account_ruc=account_ruc, ruc=ruc)
        if self.instance is not None:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise serializers.ValidationError(
                "Ya tienes registrado un proveedor con ese RUC."
            )
        return ruc

    class Meta:
        model = Supplier
        fields = (
            "id", "ruc", "alias", "display_name", "is_tracked", "notes",
            "business_name", "trade_name", "taxpayer_type", "fiscal_address",
            "economic_activities", "registered_on", "started_activities_on",
            "status", "condition", "has_issue",
            "last_checked_at", "last_changed_at", "last_error",
            "purchases_total", "purchases_count", "last_purchase_on",
            "accept_risk",
            "created_at", "updated_at",
        )
        # Everything SUNAT owns is read-only; only the registry fields are editable.
        read_only_fields = (
            "business_name", "trade_name", "taxpayer_type", "fiscal_address",
            "economic_activities", "registered_on", "started_activities_on",
            "status", "condition", "has_issue",
            "last_checked_at", "last_changed_at", "last_error",
            "created_at", "updated_at",
        )


class SupplierDetailSerializer(SupplierSerializer):
    latest_checks = serializers.SerializerMethodField()

    class Meta(SupplierSerializer.Meta):
        fields = SupplierSerializer.Meta.fields + ("latest_checks",)

    def get_latest_checks(self, obj: Supplier) -> list[dict]:
        checks = obj.checks.all()[:30]
        return SupplierCheckSerializer(checks, many=True).data


class CompraPorProveedorSerializer(serializers.Serializer):
    """Un emisor al que se le compra y que todavía no está en la cartera."""

    ruc = serializers.CharField()
    business_name = serializers.CharField()
    comprobantes = serializers.IntegerField()
    total = serializers.DecimalField(max_digits=16, decimal_places=2)
    igv_estimado = serializers.DecimalField(max_digits=16, decimal_places=2)
    ultima_compra = serializers.DateField(allow_null=True)


class FacturaEnRiesgoSerializer(serializers.Serializer):
    ruc_proveedor = serializers.CharField()
    proveedor = serializers.CharField()
    comprobante = serializers.CharField()
    fecha = serializers.DateField(allow_null=True)
    total = serializers.DecimalField(max_digits=16, decimal_places=2)
    igv_estimado = serializers.DecimalField(max_digits=16, decimal_places=2)
    estado_hoy = serializers.CharField()
    condicion_hoy = serializers.CharField()
    estado_en_la_fecha = serializers.CharField()
    condicion_en_la_fecha = serializers.CharField()
    confirmado_en_la_fecha = serializers.BooleanField()


class ResumenRiesgoSerializer(serializers.Serializer):
    """Los totales del conjunto completo, no de la página que se está viendo."""

    proveedores = serializers.IntegerField()
    comprobantes = serializers.IntegerField()
    total = serializers.DecimalField(max_digits=16, decimal_places=2)
    igv_estimado = serializers.DecimalField(max_digits=16, decimal_places=2)
    confirmados = serializers.IntegerField()


class AltaMasivaSerializer(serializers.Serializer):
    """RUC a incorporar a la cartera de una vez, desde el descubrimiento.

    Se valida el dígito de control aquí también: el alta masiva no pasa por el
    ``SupplierSerializer``, y un RUC mal escrito entraría al registro para no
    consultarse nunca contra SUNAT.
    """

    rucs = serializers.ListField(
        child=serializers.CharField(max_length=11), allow_empty=False, max_length=200,
    )

    def validate_rucs(self, value: list[str]) -> list[str]:
        limpios = [ruc.strip() for ruc in value]
        invalidos = [ruc for ruc in limpios if not is_valid_ruc(ruc)]
        if invalidos:
            raise serializers.ValidationError(
                f"RUC con formato inválido: {', '.join(invalidos)}."
            )
        return limpios
