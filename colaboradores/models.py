"""El registro de trabajadores de la empresa, con su sueldo.

Hasta ahora «los colaboradores» eran los afiliados que devolvía AFPnet. Eso
deja fuera a quien todavía no está en ninguna administradora: el que acaba de
entrar y tiene diez días para elegir régimen, el que está en ONP —que es la
otra mitad del sistema y no aparece por AFPnet—, y el que simplemente no está
en nada. Esa gente cobra un sueldo desde el primer día, así que tiene que poder
registrarse aunque ninguna fuente externa la conozca.

De ahí esta tabla, separada de ``AfpnetAffiliate``: aquella guarda **lo que dice
AFPnet** y la reescribe cada sincronización; esta guarda **lo que la empresa
sabe de su gente**, incluido el sueldo, que no viene de ningún portal. Cuando
la persona sí está en una AFP, las dos se enlazan por CUSPP y el sueldo se
rellena solo con la última remuneración declarada —hasta que alguien lo
corrija a mano, y entonces manda la mano.
"""

from __future__ import annotations

from django.db import models

from afpnet.models import Afp
from core.models import BaseModel


class RegimenPensionario(models.TextChoices):
    """Dónde aporta la persona. Son las tres situaciones reales.

    ``SIN_REGIMEN`` no es un hueco de datos: es el estado de quien acaba de ser
    contratado y aún no ha elegido. Distinguirlo de «no lo sé» importa porque
    es el único que caduca —hay un plazo legal para decidir— y es justo el que
    conviene tener a la vista.
    """

    AFP = "afp", "AFP"
    ONP = "onp", "ONP"
    SIN_REGIMEN = "sin_regimen", "Todavía sin régimen"


class OrigenSueldo(models.TextChoices):
    """De dónde salió el importe que se muestra.

    Se guarda porque decide quién puede pisarlo: lo que trajo AFPnet se
    actualiza solo en cada sincronización, lo que escribió una persona no se
    toca nunca sin que ella lo pida.
    """

    MANUAL = "manual", "Registrado por la empresa"
    AFPNET = "afpnet", "Última remuneración declarada en AFPnet"


class Colaborador(BaseModel):
    """Un trabajador de la empresa, esté o no afiliado a una AFP."""

    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)

    document_type = models.CharField(max_length=20, blank=True, default="DNI")
    document_number = models.CharField(max_length=20, blank=True, db_index=True)
    full_name = models.CharField("nombre completo", max_length=255)
    position = models.CharField("cargo", max_length=120, blank=True)
    hired_on = models.DateField("fecha de ingreso", null=True, blank=True)
    # Dato de la empresa, no de AFPnet: alimenta el card de cumpleaños y se
    # puede escribir también en fichas enlazadas al portal.
    birth_date = models.DateField("fecha de nacimiento", null=True, blank=True)

    regimen = models.CharField(
        "régimen pensionario", max_length=12, choices=RegimenPensionario,
        default=RegimenPensionario.SIN_REGIMEN,
    )
    # Solo tiene sentido con régimen AFP; en los demás queda en blanco.
    afp = models.CharField(max_length=12, choices=Afp, blank=True)
    cuspp = models.CharField(
        "CUSPP", max_length=20, blank=True, db_index=True,
        help_text="Lo pone la sincronización con AFPnet; no se escribe a mano.",
    )

    monthly_salary = models.DecimalField(
        "sueldo mensual", max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Nulo mientras no se sepa: no es lo mismo que cobrar cero.",
    )
    salary_source = models.CharField(
        max_length=10, choices=OrigenSueldo, default=OrigenSueldo.MANUAL
    )
    salary_period = models.CharField(
        max_length=6, blank=True,
        help_text="Devengue del que se tomó el sueldo, cuando vino de AFPnet.",
    )
    salary_updated_at = models.DateTimeField(null=True, blank=True)

    is_active = models.BooleanField("en planilla", default=True)
    notes = models.TextField("notas", blank=True)

    # --- Payroll engine fields (SPEC_PAYROLL_ENGINE §1.2). English names on
    # purpose: the naming rule applies to everything new, even on this
    # Spanish-named model. All optional so existing records keep working.
    terminated_on = models.DateField(
        "fecha de cese", null=True, blank=True,
        help_text="Permite prorratear el mes de salida y calcular truncos; "
                  "no es lo mismo que el vencimiento del contrato.",
    )
    pension_commission_type = models.CharField(
        "tipo de comisión AFP", max_length=10, blank=True,
        choices=[("flow", "Comisión sobre flujo"), ("mixed", "Comisión mixta")],
        help_text="Dato del afiliado, no de la AFP. Solo con régimen AFP.",
    )
    has_eps = models.BooleanField(
        "afiliado a EPS", default=False,
        help_text="Cambia la tasa del aporte de salud del empleador.",
    )
    subject_to_sctr = models.BooleanField(
        "realiza trabajo de riesgo (SCTR)", default=False,
    )
    receives_family_allowance = models.BooleanField(
        "percibe asignación familiar", default=False,
    )
    bank_name = models.CharField("banco", max_length=80, blank=True)
    bank_account_number = models.CharField(
        "cuenta bancaria", max_length=30, blank=True,
        help_text="Texto, nunca número: conserva los ceros a la izquierda.",
    )
    bank_cci = models.CharField("CCI", max_length=30, blank=True)
    # SPEC_FINANCIAL_DASHBOARD §2.1: where this person's labour cost lands
    # in the income statement. Without it, payroll enters as one block and
    # ruins the gross margin.
    expense_classification = models.CharField(
        "clasificación del gasto", max_length=15, default="administrative",
        choices=[
            ("cost_of_sales", "Costo de venta"),
            ("administrative", "Gastos administrativos"),
            ("selling", "Gastos de ventas"),
        ],
        help_text="A qué renglón del Estado de Resultados va su costo laboral.",
    )

    class Meta:
        ordering = ["full_name", "document_number"]
        verbose_name = "colaborador"
        verbose_name_plural = "colaboradores"
        constraints = [
            # Las dos llaves son únicas *dentro de la empresa* y solo cuando
            # existen: un colaborador dado de alta a mano puede no tener CUSPP
            # todavía, y varios sin CUSPP no se estorban entre sí.
            models.UniqueConstraint(
                fields=["taxpayer_id", "document_number"],
                condition=~models.Q(document_number=""),
                name="unique_colaborador_documento",
            ),
            models.UniqueConstraint(
                fields=["taxpayer_id", "cuspp"],
                condition=~models.Q(cuspp=""),
                name="unique_colaborador_cuspp",
            ),
        ]
        indexes = [models.Index(fields=["taxpayer_id", "is_active"])]

    def __str__(self) -> str:
        return f"{self.full_name} · {self.get_regimen_display()}"

    @property
    def en_afpnet(self) -> bool:
        """¿Lo conoce AFPnet? Es lo que decide si su sueldo puede venir solo."""
        return bool(self.cuspp)


class TipoContrato(models.TextChoices):
    INDEFINIDO = "indefinido", "Indefinido"
    SUJETO_A_MODALIDAD = "sujeto_a_modalidad", "Sujeto a modalidad"
    TIEMPO_PARCIAL = "tiempo_parcial", "Tiempo parcial"
    PRACTICAS = "practicas", "Prácticas / formativo"
    OTRO = "otro", "Otro"


class EstadoContrato(models.TextChoices):
    """El estado no se guarda: se calcula de las fechas cada vez que se lee,
    así nunca se queda desactualizado como en el Excel."""

    INDEFINIDO = "indefinido", "Sin vencimiento"
    VIGENTE = "vigente", "Vigente"
    POR_VENCER = "por_vencer", "Por vencer"
    VENCIDO = "vencido", "Vencido"


# Con cuántos días de anticipación un contrato pasa a «por vencer». Treinta
# porque renovar exige redactar, firmar y comunicar al trabajador antes del
# vencimiento; avisar la última semana no deja tiempo para nada de eso.
DIAS_AVISO_VENCIMIENTO = 30


class Contrato(BaseModel):
    """Un contrato laboral de un colaborador, con su archivo firmado.

    Es el «control de vencimiento de contratos» que la empresa llevaba en
    Excel. Lo derivado (duración, días para vencer, estado) no se guarda: se
    calcula de las fechas al leer, que es lo que el Excel no podía hacer solo.
    El archivo se sube aquí y solo se descarga por la API, con los permisos
    de la empresa: un contrato no puede quedar en una URL pública.
    """

    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)
    colaborador = models.ForeignKey(
        Colaborador, related_name="contratos", on_delete=models.CASCADE
    )

    tipo = models.CharField(
        max_length=20, choices=TipoContrato, default=TipoContrato.SUJETO_A_MODALIDAD
    )
    causa_objetiva = models.CharField(
        "causa objetiva", max_length=200, blank=True,
        help_text="Obligatoria en contratos sujetos a modalidad.",
    )

    fecha_inicio = models.DateField()
    fecha_fin = models.DateField(
        null=True, blank=True, help_text="Vacía en contratos indefinidos."
    )

    # None = todavía no se decide; el Excel lo dejaba en blanco.
    renovar = models.BooleanField("¿renovar?", null=True, blank=True)
    nueva_fecha_fin = models.DateField(
        "nueva fecha de fin", null=True, blank=True,
        help_text="Si se renueva: manda sobre fecha_fin para el vencimiento.",
    )
    fecha_comunicacion = models.DateField(
        "fecha de comunicación al trabajador", null=True, blank=True
    )

    archivo = models.FileField(upload_to="contratos/%Y/%m/", blank=True)
    notas = models.TextField(blank=True)

    class Meta:
        ordering = ["-fecha_inicio", "-created_at"]
        verbose_name = "contrato"
        verbose_name_plural = "contratos"
        indexes = [models.Index(fields=["taxpayer_id", "colaborador"])]

    def __str__(self) -> str:
        return f"{self.get_tipo_display()} · {self.colaborador_id}"

    @property
    def fecha_fin_vigente(self):
        """La fecha que manda para el vencimiento: la renovada si existe."""
        return self.nueva_fecha_fin or self.fecha_fin

    @property
    def duracion_meses(self) -> int | None:
        """Meses entre el inicio y el fin vigente, redondeados hacia abajo."""
        fin = self.fecha_fin_vigente
        if not fin or not self.fecha_inicio:
            return None
        meses = (fin.year - self.fecha_inicio.year) * 12 + (
            fin.month - self.fecha_inicio.month
        )
        if fin.day < self.fecha_inicio.day:
            meses -= 1
        return max(meses, 0)

    @property
    def dias_para_vencer(self) -> int | None:
        from django.utils import timezone

        fin = self.fecha_fin_vigente
        if not fin:
            return None
        return (fin - timezone.localdate()).days

    @property
    def estado(self) -> str:
        dias = self.dias_para_vencer
        if dias is None:
            return EstadoContrato.INDEFINIDO
        if dias < 0:
            return EstadoContrato.VENCIDO
        if dias <= DIAS_AVISO_VENCIMIENTO:
            return EstadoContrato.POR_VENCER
        return EstadoContrato.VIGENTE


class TipoMemorandum(models.TextChoices):
    """Qué clase de comunicación es. Decide el tono del listado, no el flujo."""

    LLAMADA_ATENCION = "llamada_atencion", "Llamada de atención"
    AMONESTACION = "amonestacion", "Amonestación escrita"
    SUSPENSION = "suspension", "Suspensión"
    FELICITACION = "felicitacion", "Felicitación"
    COMUNICACION = "comunicacion", "Comunicación interna"
    OTRO = "otro", "Otro"


class Memorandum(BaseModel):
    """Un memorándum o comunicación interna dirigida a un colaborador.

    Es el control que la empresa llevaba en Excel: número correlativo, fecha,
    tipo, motivo y el rastro de la entrega (si se entregó, cuándo y si firmó
    la recepción). El documento en sí no se sube aquí: ``archivo`` guarda la
    ruta o el enlace donde vive.
    """

    taxpayer_id = models.CharField("RUC", max_length=11, db_index=True)
    colaborador = models.ForeignKey(
        Colaborador, related_name="memorandums", on_delete=models.CASCADE
    )

    numero = models.CharField(
        "n° de memorándum", max_length=30,
        help_text="MEMO-2026-001. Se genera solo si el alta no lo trae.",
    )
    fecha_emision = models.DateField("fecha de emisión")
    tipo = models.CharField(
        max_length=20, choices=TipoMemorandum, default=TipoMemorandum.LLAMADA_ATENCION
    )
    asunto = models.CharField("motivo / asunto", max_length=200)
    descripcion = models.TextField("descripción breve", blank=True)

    entregado = models.BooleanField(default=False)
    fecha_entrega = models.DateField(null=True, blank=True)
    firmado = models.BooleanField(
        "firmado de recepción", default=False,
        help_text="El colaborador firmó el cargo de recepción.",
    )

    archivo = models.CharField(
        "archivo / ruta", max_length=255, blank=True,
        help_text="Ruta o enlace al documento firmado.",
    )

    class Meta:
        ordering = ["-fecha_emision", "-created_at"]
        verbose_name = "memorándum"
        verbose_name_plural = "memorándums"
        constraints = [
            models.UniqueConstraint(
                fields=["taxpayer_id", "numero"],
                name="unique_memorandum_numero",
            )
        ]
        indexes = [models.Index(fields=["taxpayer_id", "colaborador"])]

    def __str__(self) -> str:
        return f"{self.numero} · {self.get_tipo_display()}"

    @staticmethod
    def siguiente_numero(taxpayer_id: str, year: int) -> str:
        """El siguiente correlativo del año para la empresa: MEMO-AAAA-NNN.

        Se calcula sobre el máximo existente y no sobre el conteo: borrar un
        memorándum antiguo no puede hacer que el siguiente número repita uno
        que ya está en uso.
        """
        prefijo = f"MEMO-{year}-"
        numeros = Memorandum.objects.filter(
            taxpayer_id=taxpayer_id, numero__startswith=prefijo
        ).values_list("numero", flat=True)
        mayor = 0
        for numero in numeros:
            sufijo = numero.removeprefix(prefijo)
            if sufijo.isdigit():
                mayor = max(mayor, int(sufijo))
        return f"{prefijo}{mayor + 1:03d}"
