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

    # Programa de referidos: el código que esta persona comparte y quién la
    # trajo. El código se genera al crear la cuenta (ver billing.services).
    referral_code = models.CharField("código de referido", max_length=12, unique=True, blank=True)
    referred_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="referred_users",
    )

    # Asientos de empresa adicionales que este titular tiene por encima de los
    # que incluye su plan. Los otorga el dueño del sistema desde el admin
    # (cada empresa extra cuesta lo que fije ``BILLING_EXTRA_COMPANY_PRICE``).
    extra_company_seats = models.PositiveSmallIntegerField(
        "asientos de empresa extra", default=0,
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        ordering = ["email"]

    def __str__(self) -> str:
        return self.email

    def save(self, *args, **kwargs):
        if not self.referral_code:
            from billing.services import new_referral_code

            self.referral_code = new_referral_code()
        super().save(*args, **kwargs)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def owned_organizations_count(self) -> int:
        """Empresas de las que es titular; es lo que consume asientos."""
        return self.memberships.filter(is_active=True, role=Role.OWNER).count()

    @property
    def company_seat_limit(self) -> int:
        from billing.services import company_seat_limit

        return company_seat_limit(self)


class OrganizationQuerySet(models.QuerySet):
    def for_user(self, user) -> "OrganizationQuerySet":
        """Las organizaciones a las que este usuario tiene acceso. Es el único
        camino permitido para llegar a una organización desde un request."""
        if not user or not user.is_authenticated:
            return self.none()
        return self.filter(memberships__user=user, memberships__is_active=True)


class TaxRegime(models.TextChoices):
    """Régimen tributario. **No sale de ninguna fuente que sincronicemos**:
    la ficha RUC no lo publica. Lo declara la empresa una vez y se guarda,
    porque de él dependen los vencimientos que le tocan —el RER y el RUS no
    presentan Declaración Jurada Anual— y preguntarlo en cada carga de pantalla
    convertía el calendario en un formulario."""

    RUS = "RUS", "Nuevo RUS"
    RER = "RER", "Régimen Especial"
    RMT = "RMT", "MYPE Tributario"
    RG = "RG", "Régimen General"


def _nuevo_token_calendario() -> str:
    """Token de suscripción al calendario.

    Va en la URL de un .ics que abren Google Calendar y Apple Calendar, y esos
    clientes **no mandan cabeceras de autenticación**: la URL es la única
    credencial. Por eso es largo y aleatorio, y se puede rotar sin tocar nada
    más. Lo que expone es un cronograma derivado del dígito del RUC, no datos
    contables, pero aun así no debe ser adivinable a partir del RUC.
    """
    return secrets.token_urlsafe(32)


class Organization(BaseModel):
    """Una empresa. El RUC es la llave con la que se cruzan los datos
    scrapeados, así que es único en todo el sistema."""

    ruc = models.CharField(
        "RUC", max_length=11, unique=True, validators=[validate_ruc]
    )
    name = models.CharField("razón social", max_length=200, blank=True)
    trade_name = models.CharField("nombre comercial", max_length=200, blank=True)
    tax_regime = models.CharField(
        "régimen tributario",
        max_length=3,
        choices=TaxRegime,
        blank=True,
        help_text="Vacío mientras no se haya leído de SUNAT ni declarado.",
    )

    class RegimeSource(models.TextChoices):
        SUNAT = "sunat", "Leído de la Ficha RUC (SOL)"
        USUARIO = "usuario", "Declarado por el usuario"

    # De dónde salió el régimen: leído de la Ficha RUC en SOL (manda) o
    # declarado a mano. Y cuándo se comprobó por última vez con SUNAT.
    tax_regime_source = models.CharField(max_length=10, choices=RegimeSource, blank=True)
    tax_regime_checked_at = models.DateTimeField(null=True, blank=True)
    calendar_token = models.CharField(
        max_length=64,
        unique=True,
        default=_nuevo_token_calendario,
        help_text="Credencial de la URL de suscripción al calendario.",
    )
    # Cuántas sincronizaciones manuales gratis al día. Vacío = el default del
    # sistema (``SYNC_MANUAL_DAILY_LIMIT``). Se sube por empresa desde el admin;
    # pasado el tope, cada sincronización manual adicional genera un cargo.
    manual_sync_daily_limit = models.PositiveSmallIntegerField(
        "límite de sincronizaciones manuales al día", null=True, blank=True,
    )

    objects = OrganizationQuerySet.as_manager()

    class Meta:
        ordering = ["name", "ruc"]

    def __str__(self) -> str:
        return f"{self.ruc} · {self.name or 'sin razón social'}"

    @property
    def display_name(self) -> str:
        return self.trade_name or self.name or self.ruc

    def rotar_token_calendario(self) -> str:
        """Invalida la URL de suscripción anterior. Es el único remedio si esa
        URL se compartió por error: no se puede «cerrar sesión» en el
        calendario de otro."""
        self.calendar_token = _nuevo_token_calendario()
        self.save(update_fields=["calendar_token", "updated_at"])
        return self.calendar_token


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
    # Quién sumó a esta persona a la empresa (el titular que la invitó), para
    # dejar rastro de cómo llegó cada acceso.
    invited_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="memberships_granted",
    )

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


def _nuevo_token_invitacion() -> str:
    return secrets.token_urlsafe(24)


class InvitationStatus(models.TextChoices):
    PENDING = "pending", "Pendiente"
    ACCEPTED = "accepted", "Aceptada"
    REVOKED = "revoked", "Revocada"


class Invitation(BaseModel):
    """Invitación a una persona para acceder a **una** empresa.

    Si el correo ya tiene cuenta, la invitación se convierte en ``Membership``
    de inmediato. Si no, queda pendiente y se convierte cuando esa persona se
    registra o inicia sesión con ese correo. El invitado solo verá esa empresa;
    su acceso se resuelve siempre desde sus memberships (ver ``accounts.tenancy``).
    """

    organization = models.ForeignKey(
        Organization, related_name="invitations", on_delete=models.CASCADE
    )
    email = models.EmailField("correo")
    role = models.CharField(max_length=15, choices=Role, default=Role.VIEWER)
    invited_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="sent_invitations",
    )
    token = models.CharField(max_length=64, unique=True, default=_nuevo_token_invitacion)
    status = models.CharField(
        max_length=10, choices=InvitationStatus, default=InvitationStatus.PENDING
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "invitación"
        verbose_name_plural = "invitaciones"
        constraints = [
            # Una sola invitación viva por correo y empresa; reinvitar reusa la
            # misma fila. Las aceptadas/revocadas no estorban.
            models.UniqueConstraint(
                fields=["organization", "email"],
                condition=models.Q(status="pending"),
                name="unique_pending_invitation",
            )
        ]

    def __str__(self) -> str:
        return f"{self.email} → {self.organization.ruc} ({self.role})"


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


class SunatAuthorization(BaseModel):
    """Constancia de que la empresa autorizó el acceso a SUNAT (y demás
    portales) con sus credenciales. Ver ``accounts.services.consent``.

    Inmutable: se crea una por cada aceptación y nunca se edita; desconectar
    SUNAT la deja anotada como revocada, no la borra."""

    organization = models.ForeignKey(Organization, related_name="sunat_authorizations", on_delete=models.CASCADE)
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="sunat_authorizations")
    sol_username = models.CharField(max_length=60, blank=True)
    version = models.CharField(max_length=10)
    text_sha256 = models.CharField(max_length=64)
    scopes = models.JSONField(default=list, blank=True)
    accepted_at = models.DateTimeField(default=timezone.now)
    ip_address = models.CharField(max_length=45, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-accepted_at"]
        verbose_name = "autorización SUNAT"
        verbose_name_plural = "autorizaciones SUNAT"

    def __str__(self) -> str:
        return f"{self.organization.ruc} · v{self.version} · {self.accepted_at:%Y-%m-%d}"


class BusinessProfile(BaseModel):
    """Un perfil breve del negocio, respondido al crear la empresa.

    No sale de SUNAT: lo declara la persona para que el producto sepa qué guías
    y obligaciones tienen sentido activar (no toda empresa necesita lo mismo).
    Es opcional y editable; el módulo de cumplimiento lo lee como una señal más,
    nunca como fuente de verdad tributaria."""

    class Offering(models.TextChoices):
        PRODUCTS = "products", "Productos"
        SERVICES = "services", "Servicios"
        FOOD = "food", "Comida o bebidas"
        MIXED = "mixed", "Un poco de todo"
        UNSURE = "unsure", "No estoy seguro"

    class Sector(models.TextChoices):
        COMMERCE = "commerce", "Comercio"
        SERVICES = "services", "Servicios"
        MANUFACTURING = "manufacturing", "Manufactura"
        FOOD = "food", "Alimentos"
        CONSTRUCTION = "construction", "Construcción"
        OTHER = "other", "Otro"

    class Goal(models.TextChoices):
        ORDER_NUMBERS = "order_numbers", "Ordenar mis números"
        TAX_READY = "tax_ready", "Prepararme para impuestos"
        CASHFLOW = "cashflow", "No quedarme sin caja"
        GROWTH = "growth", "Crecer con más claridad"
        PROFITABILITY = "profitability", "Saber si vendo con ganancia"

    class Age(models.TextChoices):
        STARTING = "starting", "Estoy empezando"
        SELLING = "selling", "Ya estoy vendiendo"
        ESTABLISHED = "established", "Empresa establecida"

    organization = models.OneToOneField(
        Organization, related_name="business_profile", on_delete=models.CASCADE
    )
    offering = models.CharField(max_length=15, choices=Offering, blank=True)
    # Un negocio puede identificarse con varios rubros (restaurante que además
    # hace catering y vende insumos) y querer mejorar varias cosas a la vez.
    # Se guardan en el orden en que la persona los eligió: el primero es el
    # que más la representa y manda cuando hay que priorizar algo visual.
    # No son las actividades económicas de la Ficha RUC —esas las trae SUNAT—,
    # sino cómo la persona describe su negocio con sus palabras.
    sectors = models.JSONField("rubros", default=list, blank=True)
    goals = models.JSONField("objetivos", default=list, blank=True)
    # Espejo del primer elemento de cada lista, para el admin y para reglas o
    # reportes que todavía leen un solo valor. Se recalculan al guardar.
    sector = models.CharField(max_length=15, choices=Sector, blank=True, editable=False)
    primary_goal = models.CharField(max_length=15, choices=Goal, blank=True, editable=False)
    business_age = models.CharField(max_length=15, choices=Age, blank=True)
    people_count = models.PositiveSmallIntegerField(default=1)
    # Tri-estado a propósito (True/False/None): None = «no lo sé todavía». El
    # motor de obligaciones nunca convierte esa ausencia en un «no»; deja la
    # obligación «por determinar» con la pregunta pendiente.
    sells_to_consumers = models.BooleanField(
        "vende al consumidor final", null=True, blank=True, default=None)
    has_premises = models.BooleanField(
        "atiende en local físico", null=True, blank=True, default=None)
    sells_online = models.BooleanField(
        "vende por internet", null=True, blank=True, default=None)
    # «¿Tienes trabajadores en planilla?». Decide qué se enseña (el acceso a
    # SUNAFIL, las obligaciones laborales); la planilla real, si existe, manda.
    has_payroll = models.BooleanField(
        "tiene trabajadores en planilla", null=True, blank=True, default=None)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "perfil del negocio"
        verbose_name_plural = "perfiles del negocio"

    MAX_SECTORS = 3

    def __str__(self) -> str:
        return f"{self.organization.ruc} · {self.sector or 'sin rubro'}"

    def save(self, *args, **kwargs):
        self.sectors = list(self.sectors or [])
        self.goals = list(self.goals or [])
        self.sector = self.sectors[0] if self.sectors else ""
        self.primary_goal = self.goals[0] if self.goals else ""
        super().save(*args, **kwargs)

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None
