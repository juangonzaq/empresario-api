"""Lo que la empresa **efectivamente** presentó y pagó a SUNAT.

Sale de la «Consulta de Declaraciones y Pagos» de SOL (opción 55.2.1.1.1),
que devuelve una fila por formulario presentado —621, PLAME, boletas 1662,
detracciones— con su número de orden, fecha, banco e importe pagado. Y algo
que la pantalla no enseña: las **casillas** del formulario, de donde salen la
base de ventas, el IGV y el pago a cuenta que se declararon.

Es la tercera pata que le faltaba a Finanzas: se tenía lo facturado (CPE/SIRE),
lo bancario (ITF/conciliación) y lo que debería pasar (calendario), pero no
lo que de verdad se declaró. Se guarda tal cual vino —una fila por número de
orden— y las lecturas (declarado por periodo, evidencia de cumplimiento,
alertas) se derivan de aquí, nunca al revés.
"""

from __future__ import annotations

from django.db import models

from core.models import BaseModel


class Formulario(models.TextChoices):
    PLAME = "0601", "PLAME · Planilla electrónica"
    IGV_RENTA = "0621", "F.V. 621 · IGV-Renta mensual"
    BOLETA = "1662", "Boleta de pago virtual"
    DETRACCION = "detr", "Detracción"


class DeclaracionQuerySet(models.QuerySet):
    def de(self, account_ruc: str) -> "DeclaracionQuerySet":
        return self.filter(account_ruc=account_ruc)

    def formulario(self, codigo: str) -> "DeclaracionQuerySet":
        return self.filter(formulario=codigo)


class DeclaracionPresentada(BaseModel):
    """Una presentación o pago registrado en SUNAT, identificado por su número
    de orden. Una rectificatoria es otra fila con otro número de orden; la
    vigente para un periodo es la última presentada."""

    account_ruc = models.CharField(max_length=11, db_index=True)
    periodo = models.CharField(
        max_length=6, db_index=True,
        help_text="AAAAMM. SUNAT usa AAAA13 para la declaración anual.",
    )
    formulario = models.CharField(max_length=4, db_index=True)
    descripcion = models.CharField(max_length=120, blank=True)
    nro_orden = models.CharField(max_length=20)
    fecha_presentacion = models.DateField(null=True, blank=True, db_index=True)
    fecha_pago = models.DateTimeField(null=True, blank=True)
    banco = models.CharField(max_length=120, blank=True)
    importe_pagado = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    tipo_formulario = models.CharField(max_length=4, blank=True)
    tipo_formulario_desc = models.CharField(max_length=80, blank=True)
    medio_presentacion = models.CharField(max_length=60, blank=True)
    # Cuando una boleta paga una declaración concreta, SUNAT apunta al número
    # de orden de esa declaración. Vacío si es un pago suelto.
    nro_orden_original = models.CharField(max_length=20, blank=True)
    nro_operacion_sunat = models.CharField(max_length=30, blank=True)
    nro_operacion_banco = models.CharField(max_length=40, blank=True)
    es_boleta = models.BooleanField(default=False)
    rectificatoria = models.BooleanField(default=False)

    casillas = models.JSONField(default=dict, blank=True)
    # La «constancia» del botón de la pantalla: tributos pagados con código y
    # descripción, forma de pago, tipo de declaración; en la PLAME, cuántos
    # trabajadores y pensionistas. Vacío hasta que se pide.
    constancia = models.JSONField(default=dict, blank=True)
    raw = models.JSONField(default=dict, blank=True)
    visto_el = models.DateField(help_text="Última consulta en la que apareció.")

    objects = DeclaracionQuerySet.as_manager()

    class Meta:
        verbose_name = "declaración presentada"
        verbose_name_plural = "declaraciones presentadas"
        ordering = ["-periodo", "-fecha_presentacion", "formulario"]
        constraints = [
            models.UniqueConstraint(
                fields=["account_ruc", "nro_orden"], name="unique_declaracion_orden",
            ),
        ]
        indexes = [models.Index(fields=["account_ruc", "periodo", "formulario"])]

    def __str__(self) -> str:
        return f"{self.account_ruc} {self.formulario} {self.periodo} #{self.nro_orden}"

    @property
    def es_declaracion(self) -> bool:
        return self.formulario in (Formulario.PLAME, Formulario.IGV_RENTA)


class ConsultaDeclaraciones(BaseModel):
    """Bitácora de cada consulta a SOL: qué ventana se pidió y qué trajo. Es
    lo que permite decir en pantalla «al día de X» y no dejar un silencio."""

    account_ruc = models.CharField(max_length=11, db_index=True)
    periodo_desde = models.CharField(max_length=6)
    periodo_hasta = models.CharField(max_length=6)
    filas = models.PositiveIntegerField(default=0)
    nuevas = models.PositiveIntegerField(default=0)
    succeeded = models.BooleanField(default=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "consulta de declaraciones"
        verbose_name_plural = "consultas de declaraciones"

    def __str__(self) -> str:
        return f"{self.account_ruc} {self.periodo_desde}-{self.periodo_hasta} ({self.filas})"


class DeclaracionAnual(BaseModel):
    """La Declaración Jurada Anual de Renta (F.V. 710 empresas / 709 personas)
    tal como quedó presentada en la plataforma ``e-renta`` de SUNAT.

    Las **casillas** son el formulario entero: Balance General (359–426),
    Estado de Resultados (461–493) e impuesto y determinación de la deuda
    (100–180). Es el único sitio donde la empresa le dice a SUNAT cuánto ganó
    y cuánto tiene, con firma; por eso vale como cierre contra el que cruzar
    lo que Finanzas calcula desde comprobantes.

    El zip que ofrece la pantalla (reporte en PDF + anexos en Excel: socios,
    pagos previos, alquileres, donaciones, deudas del art. 40) se guarda tal
    cual y sus tablas se leen a ``anexos``.
    """

    account_ruc = models.CharField(max_length=11, db_index=True)
    ejercicio = models.CharField(max_length=4, db_index=True)
    formulario = models.CharField(max_length=4, default="0710")
    nro_orden = models.CharField(max_length=20)
    id_presentacion = models.CharField(max_length=40, blank=True)
    tipo_declaracion = models.CharField(max_length=40, blank=True)
    rectificatoria = models.BooleanField(default=False)
    fecha_presentacion = models.DateTimeField(null=True, blank=True)
    medio_pago = models.CharField(max_length=80, blank=True)
    importe_pagado = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    casillas = models.JSONField(default=dict, blank=True)
    tributos = models.JSONField(default=list, blank=True)
    anexos = models.JSONField(default=dict, blank=True)
    archivo = models.FileField(upload_to="renta_anual/%Y/", blank=True)
    raw_resumen = models.JSONField(default=dict, blank=True)
    raw_detallado = models.JSONField(default=dict, blank=True)
    visto_el = models.DateField()

    class Meta:
        verbose_name = "declaración anual de renta"
        verbose_name_plural = "declaraciones anuales de renta"
        ordering = ["-ejercicio", "-fecha_presentacion"]
        constraints = [
            models.UniqueConstraint(
                fields=["account_ruc", "nro_orden"], name="unique_declaracion_anual_orden",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.account_ruc} F.V. {self.formulario} {self.ejercicio} #{self.nro_orden}"
