"""Lectura y escritura del registro de colaboradores."""

from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from .models import (
    Colaborador, Contrato, Memorandum, RegimenPensionario, TipoContrato,
)


class ColaboradorSerializer(serializers.ModelSerializer):
    """La ficha completa. Escribible salvo lo que pertenece a AFPnet.

    ``salary_source`` no lo manda el cliente: lo decide la vista según cómo
    llegó el importe. Que el cliente pudiera declararse «afpnet» dejaría el
    sueldo expuesto a que la siguiente sincronización lo pisara.
    """

    regimen_label = serializers.CharField(source="get_regimen_display", read_only=True)
    afp_label = serializers.CharField(source="get_afp_display", read_only=True)
    en_afpnet = serializers.BooleanField(read_only=True)

    class Meta:
        model = Colaborador
        fields = (
            "id", "document_type", "document_number", "full_name", "position",
            "hired_on", "birth_date", "regimen", "regimen_label", "afp",
            "afp_label", "cuspp", "en_afpnet", "monthly_salary",
            "salary_source", "salary_period", "salary_updated_at",
            "is_active", "notes",
            # Payroll engine fields (SPEC_PAYROLL_ENGINE §1.2).
            "terminated_on", "pension_commission_type", "has_eps",
            "subject_to_sctr", "receives_family_allowance", "bank_name",
            "bank_account_number", "bank_cci",
            "created_at", "updated_at",
        )
        read_only_fields = (
            "cuspp", "salary_source", "salary_period", "salary_updated_at",
            "created_at", "updated_at",
        )
        extra_kwargs = {
            "full_name": {"required": True, "allow_blank": False},
            "document_number": {"required": True, "allow_blank": False},
        }

    def validate_full_name(self, value: str) -> str:
        nombre = " ".join(value.split())
        if len(nombre) < 3:
            raise serializers.ValidationError("Escribe el nombre completo.")
        return nombre

    def validate_document_number(self, value: str) -> str:
        """Único dentro de la empresa: dos fichas de la misma persona son dos
        sueldos que se contradicen, y nadie sabe cuál es el bueno."""
        documento = value.strip()
        ruc = getattr(self.context.get("request"), "ruc", None)
        repetido = Colaborador.objects.filter(
            taxpayer_id=ruc, document_number=documento
        )
        if self.instance is not None:
            repetido = repetido.exclude(pk=self.instance.pk)
        if repetido.exists():
            raise serializers.ValidationError(
                "Ya tienes un colaborador registrado con ese documento."
            )
        return documento

    def validate_birth_date(self, value):
        if value is None:
            return value
        from django.utils import timezone

        if value >= timezone.localdate():
            raise serializers.ValidationError(
                "La fecha de nacimiento tiene que ser pasada."
            )
        if value.year < 1900:
            raise serializers.ValidationError("Revisa el año de nacimiento.")
        return value

    def validate_monthly_salary(self, value: Decimal | None) -> Decimal | None:
        # Nulo es «todavía no lo sé» y se admite; cero o negativo no es un
        # sueldo, es un dato mal escrito.
        if value is not None and value <= 0:
            raise serializers.ValidationError("El sueldo debe ser mayor que cero.")
        return value

    def validate(self, attrs: dict) -> dict:
        instancia = self.instance
        tipo = attrs.get(
            "document_type", getattr(instancia, "document_type", "DNI")
        ).strip()
        numero = attrs.get(
            "document_number", getattr(instancia, "document_number", "")
        )
        if tipo.upper() == "DNI" and numero and not (
            numero.isdigit() and len(numero) == 8
        ):
            raise serializers.ValidationError(
                {"document_number": "Un DNI tiene ocho dígitos."}
            )

        regimen = attrs.get(
            "regimen", getattr(instancia, "regimen", RegimenPensionario.SIN_REGIMEN)
        )
        # Quien está en AFPnet tiene su régimen decidido por la AFP, no por
        # esta pantalla: aceptar el cambio y revertirlo en la siguiente
        # sincronización sería mentirle al usuario, así que se dice aquí.
        if instancia is not None and instancia.en_afpnet:
            if regimen != RegimenPensionario.AFP:
                raise serializers.ValidationError({
                    "regimen": (
                        f"AFPnet lo tiene afiliado a {instancia.get_afp_display()}. "
                        "Su régimen se actualiza desde ahí."
                    )
                })
            attrs["afp"] = instancia.afp
        elif regimen != RegimenPensionario.AFP:
            # La administradora solo significa algo en régimen AFP; dejarla
            # puesta al pasar a ONP daría una pantalla que se contradice.
            attrs["afp"] = ""
        attrs["document_type"] = tipo or "DNI"
        return attrs


class MemorandumSerializer(serializers.ModelSerializer):
    """Un memorándum con los datos del colaborador ya resueltos para listar.

    ``numero`` puede venir del cliente (quien migra su control en Excel ya
    tiene correlativos) o quedar en blanco y generarse solo. La unicidad se
    valida por empresa: dos memorándums con el mismo número son dos papeles
    que se contradicen.
    """

    tipo_label = serializers.CharField(source="get_tipo_display", read_only=True)
    colaborador_nombre = serializers.CharField(
        source="colaborador.full_name", read_only=True
    )
    colaborador_documento = serializers.CharField(
        source="colaborador.document_number", read_only=True
    )
    colaborador_cargo = serializers.CharField(
        source="colaborador.position", read_only=True
    )

    class Meta:
        model = Memorandum
        fields = (
            "id", "colaborador", "colaborador_nombre", "colaborador_documento",
            "colaborador_cargo", "numero", "fecha_emision", "tipo",
            "tipo_label", "asunto", "descripcion", "entregado",
            "fecha_entrega", "firmado", "archivo", "created_at", "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")
        extra_kwargs = {
            "numero": {"required": False, "allow_blank": True},
            "asunto": {"required": True, "allow_blank": False},
        }

    def validate_colaborador(self, value: Colaborador) -> Colaborador:
        # El colaborador tiene que ser de la empresa activa: aceptar el de
        # otra sería colgarle un memorándum a la planilla ajena.
        ruc = getattr(self.context.get("request"), "ruc", None)
        if value.taxpayer_id != ruc:
            raise serializers.ValidationError(
                "Ese colaborador no pertenece a tu empresa."
            )
        return value

    def validate_numero(self, value: str) -> str:
        numero = value.strip()
        if not numero:
            return ""
        ruc = getattr(self.context.get("request"), "ruc", None)
        repetido = Memorandum.objects.filter(taxpayer_id=ruc, numero=numero)
        if self.instance is not None:
            repetido = repetido.exclude(pk=self.instance.pk)
        if repetido.exists():
            raise serializers.ValidationError(
                "Ya existe un memorándum con ese número."
            )
        return numero

    def validate(self, attrs: dict) -> dict:
        instancia = self.instance
        entregado = attrs.get(
            "entregado", getattr(instancia, "entregado", False)
        )
        fecha_entrega = attrs.get(
            "fecha_entrega", getattr(instancia, "fecha_entrega", None)
        )
        firmado = attrs.get("firmado", getattr(instancia, "firmado", False))

        # Una fecha de entrega implica que se entregó; que lo diga el dato y
        # no dependa de marcar dos casillas coherentes a mano.
        if fecha_entrega and not entregado:
            attrs["entregado"] = True
        if firmado and not (entregado or fecha_entrega):
            raise serializers.ValidationError({
                "firmado": "No puede estar firmado sin haberse entregado."
            })
        return attrs


class ContratoSerializer(serializers.ModelSerializer):
    """Un contrato con todo lo derivado ya calculado para listar.

    El archivo no se escribe por aquí: se sube y se borra en el endpoint
    dedicado (multipart), y aquí solo se dice si existe y cómo se llama.
    """

    tipo_label = serializers.CharField(source="get_tipo_display", read_only=True)
    colaborador_nombre = serializers.CharField(
        source="colaborador.full_name", read_only=True
    )
    colaborador_cargo = serializers.CharField(
        source="colaborador.position", read_only=True
    )
    estado = serializers.CharField(read_only=True)
    fecha_fin_vigente = serializers.DateField(read_only=True)
    duracion_meses = serializers.IntegerField(read_only=True)
    dias_para_vencer = serializers.IntegerField(read_only=True)
    tiene_archivo = serializers.SerializerMethodField()
    archivo_nombre = serializers.SerializerMethodField()

    class Meta:
        model = Contrato
        fields = (
            "id", "colaborador", "colaborador_nombre", "colaborador_cargo",
            "tipo", "tipo_label", "causa_objetiva", "fecha_inicio",
            "fecha_fin", "fecha_fin_vigente", "duracion_meses",
            "dias_para_vencer", "estado", "renovar", "nueva_fecha_fin",
            "fecha_comunicacion", "tiene_archivo", "archivo_nombre", "notas",
            "created_at", "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")

    def get_tiene_archivo(self, contrato: Contrato) -> bool:
        return bool(contrato.archivo)

    def get_archivo_nombre(self, contrato: Contrato) -> str:
        if not contrato.archivo:
            return ""
        return contrato.archivo.name.rsplit("/", 1)[-1]

    def validate_colaborador(self, value: Colaborador) -> Colaborador:
        ruc = getattr(self.context.get("request"), "ruc", None)
        if value.taxpayer_id != ruc:
            raise serializers.ValidationError(
                "Ese colaborador no pertenece a tu empresa."
            )
        return value

    def validate(self, attrs: dict) -> dict:
        instancia = self.instance

        def vigente(campo):
            return attrs.get(campo, getattr(instancia, campo, None))

        tipo = vigente("tipo") or TipoContrato.SUJETO_A_MODALIDAD
        inicio = vigente("fecha_inicio")
        fin = vigente("fecha_fin")
        nueva_fin = vigente("nueva_fecha_fin")
        causa = (vigente("causa_objetiva") or "").strip()

        # La ley pide causa objetiva en los sujetos a modalidad (incremento
        # de actividad incluido); sin ella el contrato se entiende indefinido.
        if tipo == TipoContrato.SUJETO_A_MODALIDAD and not causa:
            raise serializers.ValidationError({
                "causa_objetiva": (
                    "Los contratos sujetos a modalidad necesitan su causa "
                    "objetiva; sin ella se entienden como indefinidos."
                )
            })
        if tipo != TipoContrato.INDEFINIDO and not fin:
            raise serializers.ValidationError({
                "fecha_fin": "Indica la fecha de fin; solo el indefinido va sin ella."
            })
        if fin and inicio and fin <= inicio:
            raise serializers.ValidationError({
                "fecha_fin": "La fecha de fin debe ser posterior al inicio."
            })
        if nueva_fin and fin and nueva_fin <= fin:
            raise serializers.ValidationError({
                "nueva_fecha_fin": (
                    "La nueva fecha de fin debe ser posterior a la actual."
                )
            })
        return attrs
