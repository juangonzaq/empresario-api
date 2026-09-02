from django.contrib import admin

from core.admin import filtro_empresa

from .models import ConsultaDeclaraciones, DeclaracionAnual, DeclaracionPresentada


@admin.register(DeclaracionPresentada)
class DeclaracionPresentadaAdmin(admin.ModelAdmin):
    list_display = (
        "account_ruc", "periodo", "formulario", "nro_orden", "fecha_presentacion",
        "banco", "importe_pagado", "rectificatoria",
    )
    list_filter = (filtro_empresa("account_ruc"), "formulario", "rectificatoria", "es_boleta")
    search_fields = ("account_ruc", "nro_orden", "periodo")
    readonly_fields = ("casillas", "raw")


@admin.register(ConsultaDeclaraciones)
class ConsultaDeclaracionesAdmin(admin.ModelAdmin):
    list_display = ("account_ruc", "periodo_desde", "periodo_hasta", "filas", "nuevas", "succeeded", "created_at")
    list_filter = (filtro_empresa("account_ruc"), "succeeded")


@admin.register(DeclaracionAnual)
class DeclaracionAnualAdmin(admin.ModelAdmin):
    list_display = ("account_ruc", "ejercicio", "formulario", "nro_orden", "fecha_presentacion", "tipo_declaracion", "importe_pagado")
    list_filter = (filtro_empresa("account_ruc"),)
    search_fields = ("account_ruc", "nro_orden", "ejercicio")
    readonly_fields = ("casillas", "tributos", "anexos", "raw_resumen", "raw_detallado")
