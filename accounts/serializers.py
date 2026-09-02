"""Entradas y salidas del API de cuentas.

Dos reglas que se repiten abajo:

* La clave SOL entra pero nunca sale. Es ``write_only`` en todas partes.
* Los flujos de correo (registro y recuperación) responden igual exista o no
  la cuenta. Si dijéramos «ese correo no está registrado» cualquiera podría
  averiguar quién es cliente nuestro probando correos.
"""

from __future__ import annotations

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from .models import (
    BusinessProfile, Membership, Organization, Role, SunatCredential, TaxRegime, User, validate_ruc,
)


def tokens_for(user: User) -> dict[str, str]:
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


class UserSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    email_verified = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id", "email", "first_name", "last_name", "phone",
            "full_name", "email_verified", "referral_code", "created_at",
        )
        read_only_fields = ("id", "email", "referral_code", "created_at")


def _password_field() -> serializers.CharField:
    """Contraseña nueva, con el mensaje en el tono del producto: el genérico
    de DRF («Asegúrese de que este campo…») ni tutea ni dice qué campo es."""
    return serializers.CharField(
        write_only=True, min_length=8,
        error_messages={
            "min_length": "La contraseña debe tener al menos 8 caracteres.",
        },
    )


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = _password_field()
    first_name = serializers.CharField(max_length=80, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=80, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    # Código de quien le habló de Empresario. Opcional; uno desconocido no
    # bloquea el registro (se ignora) para no castigar un error de tipeo.
    referral_code = serializers.CharField(max_length=12, required=False, allow_blank=True)

    def validate_email(self, value: str) -> str:
        return value.strip().lower()

    def validate_password(self, value: str) -> str:
        validate_password(value)
        return value

    def create(self, validated):
        email = validated.pop("email")
        password = validated.pop("password")
        referral_code = validated.pop("referral_code", "")
        # Un correo ya registrado no se delata aquí; la vista responde igual en
        # ambos casos y quien ya tenía cuenta recibe un aviso por correo.
        existing = User.objects.filter(email=email).first()
        if existing:
            return existing
        user = User.objects.create_user(email=email, password=password, **validated)
        if referral_code:
            from billing.services import link_referral

            link_referral(user, referral_code)
        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = authenticate(
            request=self.context.get("request"),
            username=attrs["email"].strip().lower(),
            password=attrs["password"],
        )
        if user is None:
            raise serializers.ValidationError(
                {"detail": "Correo o contraseña incorrectos."}
            )
        if not user.is_active:
            raise serializers.ValidationError(
                {"detail": "Esta cuenta está desactivada."}
            )
        attrs["user"] = user
        return attrs


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = _password_field()

    def validate_password(self, value: str) -> str:
        validate_password(value)
        return value


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    password = _password_field()

    def validate_current_password(self, value: str) -> str:
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("La contraseña actual no coincide.")
        return value

    def validate_password(self, value: str) -> str:
        validate_password(value, self.context["request"].user)
        return value


def payroll_status(org: Organization) -> bool | None:
    profile = getattr(org, "business_profile", None)
    declared = getattr(profile, "has_payroll", None)
    if declared is not None:
        return declared
    try:
        from colaboradores.models import Colaborador
    except Exception:  # pragma: no cover
        return None
    if Colaborador.objects.filter(taxpayer_id=org.ruc, is_active=True).exists():
        return True
    return None


class OrganizationSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)
    role = serializers.SerializerMethodField()
    sunat_status = serializers.SerializerMethodField()
    subscription = serializers.SerializerMethodField()
    has_payroll = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = ("id", "ruc", "name", "trade_name", "display_name", "role",
                  "sunat_status", "tax_regime", "tax_regime_source", "tax_regime_checked_at",
                  "subscription", "has_payroll")
        # El régimen lo lee la sincronización de la Ficha RUC en SOL o se
        # declara desde el calendario (PATCH /api/calendario/mio/); aquí solo se lee.
        read_only_fields = ("id", "ruc", "tax_regime", "tax_regime_source", "tax_regime_checked_at")

    def get_role(self, org: Organization) -> str | None:
        roles = self.context.get("roles") or {}
        return roles.get(org.id)

    def get_sunat_status(self, org: Organization) -> str:
        credential = getattr(org, "sunat_credential", None)
        return credential.status if credential else "sin_conectar"

    def get_has_payroll(self, org: Organization) -> bool | None:
        """¿Tiene planilla? Lo declarado en el perfil; si no lo dijo, la
        planilla cargada decide; si tampoco hay, None (no se sabe). Gatea el
        acceso a SUNAFIL en la barra."""
        return payroll_status(org)

    def get_subscription(self, org: Organization) -> dict:
        """Estado de la suscripción, para que el front sepa desde la sesión si
        hay que enseñar el aviso de prueba o llevar a pagar."""
        from billing.services import summary

        return summary(org)


class OrganizationCreateSerializer(serializers.Serializer):
    """Alta de empresa. Quien la crea queda como titular."""

    ruc = serializers.CharField(max_length=11, validators=[validate_ruc])
    name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    trade_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    # Régimen declarado al registrar. Opcional: si el usuario aún no lo sabe se
    # deja en blanco y lo lee la sincronización de la Ficha RUC. Cuando lo
    # declara, SUNAT sigue mandando y lo corrige al sincronizar.
    tax_regime = serializers.ChoiceField(
        choices=TaxRegime.choices, required=False, allow_blank=True,
    )

    def validate_ruc(self, value: str) -> str:
        ruc = value.strip()
        if Organization.objects.filter(ruc=ruc).exists():
            # El RUC es único en todo el sistema: si ya existe, el camino es
            # que alguien de esa empresa te invite, no crearla de nuevo.
            raise serializers.ValidationError(
                "Ese RUC ya está registrado. Pide que te inviten desde la "
                "empresa existente."
            )
        return ruc

    @transaction.atomic
    def create(self, validated):
        from django.utils import timezone

        regime = (validated.pop("tax_regime", "") or "").strip()
        organization = Organization.objects.create(**validated)
        if regime:
            organization.tax_regime = regime
            organization.tax_regime_source = Organization.RegimeSource.USUARIO
            organization.tax_regime_checked_at = timezone.now()
            organization.save(update_fields=[
                "tax_regime", "tax_regime_source", "tax_regime_checked_at", "updated_at",
            ])
        Membership.objects.create(
            user=self.context["request"].user,
            organization=organization,
            role=Role.OWNER,
        )
        # Si esta persona ya había ganado meses por referidos antes de tener
        # empresa, se le aplican ahora que la tiene.
        from billing.services import apply_pending_rewards

        apply_pending_rewards(self.context["request"].user)
        return organization


class SunatCredentialSerializer(serializers.ModelSerializer):
    """Estado de la conexión. La clave nunca aparece, ni enmascarada."""

    ruc = serializers.CharField(source="organization.ruc", read_only=True)

    class Meta:
        model = SunatCredential
        fields = ("ruc", "sol_username", "status", "uses_primary_user",
                  "last_verified_at", "last_error")
        read_only_fields = fields


class SunatConnectSerializer(serializers.Serializer):
    sol_username = serializers.CharField(max_length=60)
    sol_password = serializers.CharField(write_only=True, max_length=100)
    # No hay forma fiable de distinguir un usuario principal de uno secundario
    # sin preguntarle a SUNAT, así que no lo fingimos: se lo preguntamos al
    # contribuyente y lo dejamos anotado junto a la credencial.
    is_primary_user = serializers.BooleanField(
        default=False,
        help_text="El contribuyente declara que son las credenciales del usuario SOL principal.",
    )
    # Autorización expresa: sin ella no se guarda la clave. La versión que
    # se acepta tiene que ser la vigente, para que conste qué texto se leyó.
    authorization_accepted = serializers.BooleanField(default=False)
    authorization_version = serializers.CharField(max_length=10, required=False, allow_blank=True)

    def validate(self, attrs):
        from .services import consent

        if not attrs.get("authorization_accepted"):
            raise serializers.ValidationError({
                "authorization_accepted": "Debes leer y aceptar la autorización de acceso a SUNAT.",
            })
        version = (attrs.get("authorization_version") or "").strip()
        if version and version != consent.VERSION:
            raise serializers.ValidationError({
                "authorization_version": "La autorización cambió; vuelve a leerla y acéptala.",
            })
        return attrs

    def validate_sol_username(self, value: str) -> str:
        username = value.strip()
        if not username:
            raise serializers.ValidationError("Indica el usuario SOL.")
        return username


# ── Equipo: invitar y administrar accesos de una empresa ──

# El titular puede sumar contadores y gente de solo lectura; sumar otro titular
# se permite pero se exige, en la vista, que quien invita sea titular.
_ROLES_INVITABLES = [Role.OWNER, Role.ACCOUNTANT, Role.VIEWER]


class MemberInviteSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=_ROLES_INVITABLES, default=Role.VIEWER)

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class MemberRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=_ROLES_INVITABLES)


class BusinessProfileSerializer(serializers.ModelSerializer):
    """Perfil breve del negocio. Todos los campos son opcionales: se puede
    guardar a medias y completar después."""

    is_complete = serializers.BooleanField(read_only=True)
    offerings = serializers.ListField(
        child=serializers.ChoiceField(choices=BusinessProfile.Offering.choices),
        required=False, allow_empty=True,
    )
    sectors = serializers.ListField(
        child=serializers.ChoiceField(choices=BusinessProfile.Sector.choices),
        required=False, allow_empty=True,
    )
    goals = serializers.ListField(
        child=serializers.ChoiceField(choices=BusinessProfile.Goal.choices),
        required=False, allow_empty=True,
    )

    class Meta:
        model = BusinessProfile
        fields = ("offerings", "sectors", "goals", "business_age",
                  "people_count", "sells_to_consumers", "has_premises",
                  "sells_online", "has_payroll", "is_complete", "completed_at")
        read_only_fields = ("is_complete", "completed_at")

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        seen: list[str] = []
        for v in values:
            if v not in seen:
                seen.append(v)
        return seen

    def validate_offerings(self, value: list[str]) -> list[str]:
        return self._unique(value)

    def validate_sectors(self, value: list[str]) -> list[str]:
        value = self._unique(value)
        if len(value) > BusinessProfile.MAX_SECTORS:
            raise serializers.ValidationError(
                f"Elige como máximo {BusinessProfile.MAX_SECTORS} rubros.")
        return value

    def validate_goals(self, value: list[str]) -> list[str]:
        return self._unique(value)

    def validate_people_count(self, value: int) -> int:
        if value < 0 or value > 9999:
            raise serializers.ValidationError("Indica un número de personas válido.")
        return value
