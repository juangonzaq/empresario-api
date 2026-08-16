"""Lo que traemos de AFPnet, y la sesión con la que se trae.

AFPnet es donde el empleador declara y paga los aportes previsionales de sus
trabajadores a las cuatro AFP. A diferencia del resto de fuentes de este
proyecto, su login lleva un **CAPTCHA de imagen**: no se puede entrar sin que
una persona lo resuelva.

De ahí sale la pieza rara de este módulo, ``AfpnetSession``. En SUNAT guardamos
la clave y el worker entra solo cada noche; aquí lo que se guarda es la
*sesión ya abierta*, porque volver a abrirla exige a un humano. Mientras la
sesión viva, la sincronización corre desatendida como cualquier otra; cuando
caduca, la empresa tiene que volver a resolver un CAPTCHA.

Todo lo demás sigue la convención del proyecto: ``taxpayer_id`` es el RUC dueño
de la fila, y es lo que ``TenantScopedViewSetMixin`` usa para acotar.
"""

from __future__ import annotations

import datetime

from django.db import models
from django.utils import timezone

from accounts.services.crypto import decrypt, encrypt
from core.models import BaseModel

# Las cuatro administradoras del sistema privado de pensiones peruano.
class Afp(models.TextChoices):
    HABITAT = "habitat", "AFP Habitat"
    INTEGRA = "integra", "AFP Integra"
    PRIMA = "prima", "Prima AFP"
    PROFUTURO = "profuturo", "Profuturo AFP"
    DESCONOCIDA = "desconocida", "Sin identificar"


class SessionStatus(models.TextChoices):
    ACTIVE = "activa", "Activa"
    EXPIRED = "caducada", "Caducada"
    INVALID = "invalida", "Credenciales rechazadas"


# Cuánto damos por buena una sesión sin volver a comprobarla. AFPnet no publica
# su caducidad, así que se empieza con un valor conservador y se corrige con lo
# que se observe: el cliente marca la sesión como caducada en cuanto una
# petición vuelve a la pantalla de login, que es la señal de verdad.
SESSION_TTL = datetime.timedelta(hours=8)


class AfpnetSession(BaseModel):
    """La sesión abierta de una empresa en AFPnet.

    Guarda cookies, no contraseñas: la clave no se conserva porque por sí sola
    no sirve para entrar —hace falta el CAPTCHA—, y no guardarla elimina el
    incentivo de intentar automatizar ese paso más adelante.
    """

    organization = models.OneToOneField(
        "accounts.Organization",
        related_name="afpnet_session",
        on_delete=models.CASCADE,
    )
    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)
    username = models.CharField(max_length=60, blank=True)

    # Tarro de cookies serializado y cifrado con la llave del proyecto.
    encrypted_cookies = models.TextField(blank=True)

    status = models.CharField(
        max_length=10, choices=SessionStatus, default=SessionStatus.EXPIRED
    )
    opened_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["taxpayer_id"]
        verbose_name = "sesión AFPnet"
        verbose_name_plural = "sesiones AFPnet"

    def __str__(self) -> str:
        return f"AFPnet {self.taxpayer_id} · {self.get_status_display()}"

    @property
    def cookies(self) -> dict[str, str]:
        import json

        if not self.encrypted_cookies:
            return {}
        return json.loads(decrypt(self.encrypted_cookies))

    @cookies.setter
    def cookies(self, value: dict[str, str]) -> None:
        import json

        self.encrypted_cookies = encrypt(json.dumps(value)) if value else ""

    @property
    def is_usable(self) -> bool:
        """¿Merece la pena intentar usarla?

        Es una apuesta, no una certeza: la única prueba de que una sesión sigue
        viva es usarla. Sirve para no gastar una petición cuando ya sabemos que
        no puede funcionar.
        """
        if self.status != SessionStatus.ACTIVE or not self.encrypted_cookies:
            return False
        if self.opened_at is None:
            return False
        return timezone.now() - self.opened_at < SESSION_TTL

    def marcar_activa(self, cookies: dict[str, str], username: str) -> None:
        self.cookies = cookies
        self.username = username
        self.status = SessionStatus.ACTIVE
        self.opened_at = timezone.now()
        self.last_used_at = timezone.now()
        self.last_error = ""
        self.save()

    def marcar_caducada(self, motivo: str = "") -> None:
        self.status = SessionStatus.EXPIRED
        self.encrypted_cookies = ""
        self.last_error = motivo[:500]
        self.save(update_fields=[
            "status", "encrypted_cookies", "last_error", "updated_at",
        ])


class AfpnetDeclaration(BaseModel):
    """Una planilla de aportes presentada, por período y AFP.

    Se cruza con el PLAME que ya traemos de SUNAT: declarar en uno y no en el
    otro es la incoherencia que más cara sale, y hasta ahora no había forma de
    verla porque solo teníamos un lado.
    """

    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)
    period = models.CharField("período", max_length=6, db_index=True,
                              help_text="YYYYMM")
    afp = models.CharField(max_length=12, choices=Afp, default=Afp.DESCONOCIDA)

    presentation_number = models.CharField(max_length=40, blank=True)
    presented_at = models.DateField(null=True, blank=True)
    paid_on = models.DateField(null=True, blank=True)

    # Los dos importes que publica el portal: el fondo de pensiones y las
    # retenciones y retribuciones (comisión + seguro).
    nominal_fondo = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True
    )
    nominal_ryr = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(max_length=3, default="PEN")
    state = models.CharField(max_length=60, blank=True)
    worker_type = models.CharField(max_length=40, blank=True)
    ticket = models.CharField(max_length=60, blank=True)
    bank = models.CharField(max_length=60, blank=True)
    payment_method = models.CharField(max_length=20, blank=True)
    is_paid = models.BooleanField(default=False)

    raw = models.JSONField(default=dict, blank=True,
                           help_text="La fila tal como vino, para auditar el parseo.")

    class Meta:
        ordering = ["-period", "afp"]
        constraints = [
            models.UniqueConstraint(
                fields=["taxpayer_id", "period", "afp", "presentation_number"],
                name="unique_afpnet_declaration",
            )
        ]
        indexes = [models.Index(fields=["taxpayer_id", "period"])]

    def __str__(self) -> str:
        return f"{self.taxpayer_id} · {self.period} · {self.get_afp_display()}"


class AfpnetDebt(BaseModel):
    """Lo declarado y no pagado, con sus intereses.

    Es la fuente que alimenta las alertas y el calendario: una deuda
    previsional corre intereses desde el día siguiente al vencimiento y, a
    diferencia de la tributaria, es dinero retenido a los trabajadores.
    """

    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)
    period = models.CharField("período", max_length=6, db_index=True)
    afp = models.CharField(max_length=12, choices=Afp, default=Afp.DESCONOCIDA)

    concept = models.CharField(max_length=120, blank=True)
    principal = models.DecimalField(max_digits=16, decimal_places=2,
                                    null=True, blank=True)
    interest = models.DecimalField(max_digits=16, decimal_places=2,
                                   null=True, blank=True)
    total = models.DecimalField(max_digits=16, decimal_places=2,
                                null=True, blank=True)
    currency = models.CharField(max_length=3, default="PEN")
    due_on = models.DateField(null=True, blank=True)
    state = models.CharField(max_length=60, blank=True)

    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-period", "afp"]
        constraints = [
            models.UniqueConstraint(
                fields=["taxpayer_id", "period", "afp", "concept"],
                name="unique_afpnet_debt",
            )
        ]
        indexes = [models.Index(fields=["taxpayer_id", "period"])]

    def __str__(self) -> str:
        return f"Deuda {self.taxpayer_id} · {self.period} · {self.total}"


class AfpnetAffiliate(BaseModel):
    """Un trabajador afiliado, con el CUSPP que lo identifica en el sistema.

    El CUSPP es la llave del afiliado en todo el sistema privado de pensiones;
    el documento de identidad puede repetirse entre AFP por errores de
    registro, el CUSPP no.
    """

    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)
    cuspp = models.CharField("CUSPP", max_length=20, db_index=True)
    document_type = models.CharField(max_length=20, blank=True)
    document_number = models.CharField(max_length=20, blank=True, db_index=True)
    full_name = models.CharField(max_length=255, blank=True)
    afp = models.CharField(max_length=12, choices=Afp, default=Afp.DESCONOCIDA)

    # «flujo» (comisión sobre la remuneración) o «mixta» (flujo + saldo).
    commission_type = models.CharField(max_length=30, blank=True)
    affiliated_on = models.DateField(null=True, blank=True)
    state = models.CharField(max_length=60, blank=True)
    is_active = models.BooleanField(default=True)

    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["full_name", "cuspp"]
        constraints = [
            models.UniqueConstraint(
                fields=["taxpayer_id", "cuspp"], name="unique_afpnet_affiliate",
            )
        ]

    def __str__(self) -> str:
        return f"{self.full_name or self.cuspp} · {self.get_afp_display()}"


class DocumentKind(models.TextChoices):
    PRESENTATION = "constancia_presentacion", "Constancia de presentación"
    PAYMENT = "constancia_pago", "Constancia de pago"
    TICKET = "ticket", "Ticket de pago"
    OTHER = "otro", "Otro"


class AfpnetDocument(BaseModel):
    """Una constancia descargada. El archivo se guarda, no solo su enlace:
    las URL de AFPnet caducan con la sesión y un enlace muerto no es prueba de
    nada ante una fiscalización."""

    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)
    kind = models.CharField(max_length=30, choices=DocumentKind,
                            default=DocumentKind.OTHER)
    period = models.CharField(max_length=6, blank=True, db_index=True)
    afp = models.CharField(max_length=12, choices=Afp, default=Afp.DESCONOCIDA)
    number = models.CharField(max_length=60, blank=True)
    issued_on = models.DateField(null=True, blank=True)

    file = models.FileField(upload_to="afpnet/%Y/%m/", blank=True)
    checksum = models.CharField(
        max_length=64, blank=True, db_index=True,
        help_text="SHA-256 del archivo: evita volver a bajar lo que ya está.",
    )
    source_url = models.TextField(blank=True)

    class Meta:
        ordering = ["-issued_on", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["taxpayer_id", "kind", "period", "afp", "number"],
                name="unique_afpnet_document",
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} · {self.period} · {self.taxpayer_id}"


class AfpnetCompany(BaseModel):
    """Los datos que AFPnet tiene de la empresa y de su representante legal.

    Se leen del formulario de modificación —la única pantalla que los
    publica— y **solo se leen**. Interesan porque el representante legal y su
    correo son a quien AFPnet notifica: si están desactualizados, los avisos de
    deuda no llegan a nadie.
    """

    organization = models.OneToOneField(
        "accounts.Organization", related_name="afpnet_company",
        on_delete=models.CASCADE,
    )
    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)
    legal_name = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    address = models.CharField(max_length=255, blank=True)
    department = models.CharField(max_length=80, blank=True)
    province = models.CharField(max_length=80, blank=True)
    district = models.CharField(max_length=80, blank=True)

    representative = models.CharField(max_length=255, blank=True)
    representative_document = models.CharField(max_length=20, blank=True)
    representative_role = models.CharField(max_length=120, blank=True)
    representative_email = models.EmailField(blank=True)
    representative_phone = models.CharField(max_length=40, blank=True)
    second_signature = models.BooleanField(default=False)

    fetched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "empresa en AFPnet"
        verbose_name_plural = "empresas en AFPnet"

    def __str__(self) -> str:
        return f"{self.taxpayer_id} · {self.legal_name}"


class AfpnetPeriodSummary(BaseModel):
    """Resumen de situación de obligaciones de pago, por devengue.

    Es la vista que dice, mes a mes, cuántas obligaciones hay y cuántas llevan
    deuda: el histórico que permite ver de un vistazo si algún mes quedó
    descubierto.
    """

    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)
    period = models.CharField("período", max_length=6, db_index=True)
    total_op = models.PositiveIntegerField(default=0)
    op_cierta = models.PositiveIntegerField(default=0)
    op_presunta = models.PositiveIntegerField(default=0)
    op_con_deuda = models.PositiveIntegerField(default=0)
    op_sin_deuda = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-period"]
        constraints = [
            models.UniqueConstraint(
                fields=["taxpayer_id", "period"],
                name="unique_afpnet_period_summary",
            )
        ]

    def __str__(self) -> str:
        return f"{self.taxpayer_id} · {self.period} · {self.total_op} OP"


class AfpnetContribution(BaseModel):
    """El aporte de un trabajador en un mes concreto.

    Es el detalle fino que AFPnet publica por afiliado: cuánto se le obligó a
    aportar, cuánto se declaró y cuánto se pagó de verdad. La diferencia entre
    declarado y pagado es la deuda previsional de esa persona — dinero que se
    le retuvo del sueldo y no llegó a su fondo.
    """

    affiliate = models.ForeignKey(
        AfpnetAffiliate, related_name="contributions", on_delete=models.CASCADE
    )
    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)
    period = models.CharField("período", max_length=6, db_index=True)

    obligation_id = models.CharField(max_length=40, blank=True)
    # «C» cierta · «P» presunta. Una presunta es la que AFPnet asume porque no
    # se declaró: no significa que se deba, pero sí que falta declarar.
    kind = models.CharField(max_length=2, blank=True)
    has_employment = models.BooleanField(default=True)

    remuneration = models.DecimalField(max_digits=16, decimal_places=2,
                                       null=True, blank=True)
    obliged_fund = models.DecimalField(max_digits=16, decimal_places=2,
                                       null=True, blank=True)
    obliged_insurance = models.DecimalField(max_digits=16, decimal_places=2,
                                            null=True, blank=True)
    obliged_commission = models.DecimalField(max_digits=16, decimal_places=2,
                                             null=True, blank=True)
    declared_fund = models.DecimalField(max_digits=16, decimal_places=2,
                                        null=True, blank=True)
    paid_fund = models.DecimalField(max_digits=16, decimal_places=2,
                                    null=True, blank=True)
    declared_unpaid = models.DecimalField(max_digits=16, decimal_places=2,
                                          null=True, blank=True)

    class Meta:
        ordering = ["-period"]
        constraints = [
            models.UniqueConstraint(
                fields=["affiliate", "period"],
                name="unique_afpnet_contribution",
            )
        ]
        indexes = [models.Index(fields=["taxpayer_id", "period"])]

    def __str__(self) -> str:
        return f"{self.affiliate_id} · {self.period}"

    @property
    def situacion(self) -> str:
        """«pagado», «con_deuda» o «sin_dato».

        Tres estados y no un booleano porque AFPnet **no siempre publica el
        importe pagado** en este endpoint: llega en blanco aunque la planilla
        del mes figure como PAGADA. Con un booleano, esos meses salían como
        «pendiente» y alarmaban sin motivo. La señal fiable de deuda es lo
        declarado y no pagado; lo demás, cuando falta, es que no lo sabemos.
        """
        if self.declared_unpaid:
            return "con_deuda"
        if self.paid_fund:
            return "pagado"
        return "sin_dato"

    @property
    def is_paid(self) -> bool:
        return self.situacion == "pagado"
