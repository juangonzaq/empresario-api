"""Interesados que dejan sus datos en la página pública.

Es lo único del sistema que nace sin usuario ni empresa: alguien que todavía
no es cliente y quiere que le escribamos. Por eso no cuelga de ``Organization``
ni pasa por el tenant: es una bandeja de entrada comercial, se trabaja desde
el admin y se marca cuando ya se contactó.
"""

from __future__ import annotations

from django.core.validators import RegexValidator
from django.db import models

from core.models import BaseModel


class Lead(BaseModel):
    name = models.CharField("nombre", max_length=120)
    email = models.EmailField("correo")
    phone = models.CharField("teléfono / WhatsApp", max_length=30, blank=True)
    ruc = models.CharField(
        "RUC", max_length=11, blank=True,
        validators=[RegexValidator(r"^\d{11}$", "El RUC tiene 11 dígitos.")],
    )
    company = models.CharField("empresa", max_length=200, blank=True)
    message = models.TextField("mensaje", blank=True)
    # De qué formulario o campaña vino, para saber qué convierte.
    source = models.CharField("origen", max_length=60, default="landing")
    contacted_at = models.DateTimeField("contactado el", null=True, blank=True)
    notes = models.TextField("notas internas", blank=True)

    class Meta:
        verbose_name = "interesado"
        verbose_name_plural = "interesados"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"
