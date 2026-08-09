"""Identity and tenancy: who logs in, and whose data they may see.

Three pieces:

* ``User`` — a person. Logs in with an email address; there are no usernames.
* ``Organization`` — a company, identified by its RUC. **Every scraped row in
  this project belongs to exactly one organization**, matched by RUC through
  the ``taxpayer_id`` / ``account_ruc`` columns the other apps already carry.
* ``Membership`` — the link between the two, with a role. A person can belong
  to several companies (an accountant with many clients) and a company can
  have several people (owner plus accountant).

Nothing here trusts a RUC that arrives in a request. The organization is
always resolved from the caller's memberships; see ``accounts.tenancy``.
"""

from __future__ import annotations

import datetime
import secrets

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from core.models import BaseModel

validate_ruc = RegexValidator(r"^\d{11}$", "El RUC debe tener 11 dígitos.")


class UserManager(BaseUserManager):
    """Email is the identity; it is stored lowercased so logins are stable."""

    use_in_migrations = True

    def _create(self, email: str, password: str | None, **extra):
        if not email:
            raise ValueError("El correo es obligatorio.")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("email_verified_at", timezone.now())
        if not extra["is_staff"] or not extra["is_superuser"]:
            raise ValueError("Un superusuario debe tener is_staff e is_superuser.")
        return self._create(email, password, **extra)


class User(BaseModel, AbstractBaseUser, PermissionsMixin):
    email = models.EmailField("correo", unique=True)
    first_name = models.CharField("nombres", max_length=80, blank=True)
    last_name = models.CharField("apellidos", max_length=80, blank=True)
    phone = models.CharField("teléfono", max_length=20, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    # Una cuenta sin verificar puede entrar, pero no conectar SUNAT: se le
    # pide confirmar el correo antes de entregarnos credenciales.
    email_verified_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        ordering = ["email"]

    def __str__(self) -> str:
        return self.email

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None


class OrganizationQuerySet(models.QuerySet):
    def for_user(self, user) -> "OrganizationQuerySet":
        """Las organizaciones a las que este usuario tiene acceso. Es el único
        camino permitido para llegar a una organización desde un request."""
        if not user or not user.is_authenticated:
            return self.none()
        return self.filter(memberships__user=user, memberships__is_active=True)


class Organization(BaseModel):
    """Una empresa. El RUC es la llave con la que se cruzan los datos
    scrapeados, así que es único en todo el sistema."""

    ruc = models.CharField(
        "RUC", max_length=11, unique=True, validators=[validate_ruc]
    )
    name = models.CharField("razón social", max_length=200, blank=True)
    trade_name = models.CharField("nombre comercial", max_length=200, blank=True)

    objects = OrganizationQuerySet.as_manager()

    class Meta:
        ordering = ["name", "ruc"]

    def __str__(self) -> str:
        return f"{self.ruc} · {self.name or 'sin razón social'}"

    @property
    def display_name(self) -> str:
        return self.trade_name or self.name or self.ruc


class Role(models.TextChoices):
    OWNER = "owner", "Titular"
    ACCOUNTANT = "accountant", "Contador"
    VIEWER = "viewer", "Solo lectura"


class Membership(BaseModel):
    """Acceso de una persona a una empresa."""

    user = models.ForeignKey(
        User, related_name="memberships", on_delete=models.CASCADE
    )
    organization = models.ForeignKey(
        Organization, related_name="memberships", on_delete=models.CASCADE
    )
    role = models.CharField(max_length=15, choices=Role, default=Role.OWNER)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["organization__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization"], name="unique_membership"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user.email} → {self.organization.ruc} ({self.role})"

    @property
    def can_manage(self) -> bool:
        """Conectar SUNAT, invitar gente y disparar sincronizaciones."""
        return self.role in (Role.OWNER, Role.ACCOUNTANT)


def _expiry(hours: int) -> datetime.datetime:
    return timezone.now() + datetime.timedelta(hours=hours)


class TokenPurpose(models.TextChoices):
    EMAIL_VERIFICATION = "email_verification", "Verificación de correo"
    PASSWORD_RESET = "password_reset", "Recuperación de contraseña"


class OneTimeTokenQuerySet(models.QuerySet):
    def usable(self) -> "OneTimeTokenQuerySet":
        return self.filter(used_at__isnull=True, expires_at__gt=timezone.now())


class OneTimeToken(BaseModel):
    """Token de un solo uso para verificar el correo o recuperar la clave.

    Se guarda el token en claro a propósito: vive horas, no da acceso por sí
    mismo (hay que completar la acción) y poder reenviarlo por correo sin
    regenerarlo simplifica el flujo. Lo que nunca se guarda en claro es la
    contraseña ni la clave SOL.
    """

    user = models.ForeignKey(User, related_name="tokens", on_delete=models.CASCADE)
    purpose = models.CharField(max_length=25, choices=TokenPurpose)
    token = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    objects = OneTimeTokenQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "purpose"])]

    def __str__(self) -> str:
        return f"{self.get_purpose_display()} · {self.user.email}"

    @property
    def is_usable(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()

    def consume(self) -> None:
        self.used_at = timezone.now()
        self.save(update_fields=["used_at", "updated_at"])

    @classmethod
    def issue(cls, user: User, purpose: str, hours: int = 24) -> "OneTimeToken":
        """Emite un token nuevo e invalida los anteriores del mismo tipo, para
        que un enlace viejo en la bandeja no siga sirviendo."""
        cls.objects.filter(user=user, purpose=purpose, used_at__isnull=True).update(
            used_at=timezone.now()
        )
        return cls.objects.create(
            user=user,
            purpose=purpose,
            token=secrets.token_urlsafe(32),
            expires_at=_expiry(hours),
        )


class SunatConnectionStatus(models.TextChoices):
    PENDING = "pendiente", "Pendiente de validar"
    CONNECTED = "conectada", "Conectada"
    INVALID = "invalida", "Credenciales rechazadas"
    DISCONNECTED = "desconectada", "Desconectada"


class SunatCredential(BaseModel):
    """Las credenciales SOL de una empresa.

    La clave se guarda cifrada (ver ``accounts.services.crypto``) porque el
    worker debe poder enviársela a SUNAT en cada sincronización. Nunca sale por
    la API: los serializers la aceptan de escritura y jamás la devuelven.

    ``uses_primary_user`` deja constancia de si el contribuyente entregó su
    usuario SOL principal —que puede presentar declaraciones y pedir
    devoluciones— o un usuario secundario de solo consulta, que es lo
    recomendado.
    """

    organization = models.OneToOneField(
        Organization, related_name="sunat_credential", on_delete=models.CASCADE
    )
    sol_username = models.CharField("usuario SOL", max_length=60)
    encrypted_password = models.TextField("clave SOL cifrada")

    status = models.CharField(
        max_length=15, choices=SunatConnectionStatus,
        default=SunatConnectionStatus.PENDING,
    )
    uses_primary_user = models.BooleanField(
        default=False,
        help_text="El contribuyente aceptó entregar su usuario SOL principal.",
    )
    last_verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    connected_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="sunat_connections",
    )

    class Meta:
        verbose_name = "credencial SUNAT"
        verbose_name_plural = "credenciales SUNAT"

    def __str__(self) -> str:
        return f"{self.organization.ruc} · {self.get_status_display()}"

    # El texto plano solo se toca en estos dos puntos.
    def set_password(self, raw: str) -> None:
        from .services.crypto import encrypt

        self.encrypted_password = encrypt(raw)

    @property
    def password(self) -> str:
        from .services.crypto import decrypt

        return decrypt(self.encrypted_password)

    @property
    def is_usable(self) -> bool:
        return (
            self.status == SunatConnectionStatus.CONNECTED
            and bool(self.encrypted_password)
        )
