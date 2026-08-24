"""Autorización de acceso a SUNAT (y demás portales) que otorga la empresa.

Es el sustento de que la persona, en nombre de su empresa, autorizó a
Empresario a entrar con su clave SOL a leer su información. Se guarda **quién**
(usuario y usuario SOL), **cuándo**, **desde dónde** (IP, navegador) y **qué
texto exacto** aceptó (versión y huella SHA-256), para poder demostrarlo aunque
el texto cambie después.

Si cambias el texto, sube ``VERSION``: las autorizaciones anteriores siguen
ligadas a su versión y la app pedirá aceptar la nueva al reconectar.
"""

from __future__ import annotations

import hashlib

VERSION = "1.0"
TITULO = "Autorización de acceso y tratamiento de información tributaria y laboral"

# Texto en párrafos. Se muestra íntegro antes de conectar y queda enlazado en
# el perfil. Revísalo con tu asesor legal antes de salir a producción.
PARRAFOS: list[str] = [
    "Quien acepta esta autorización declara ser titular, representante legal o persona "
    "autorizada por la empresa identificada con el RUC que conecta (en adelante, «la "
    "Empresa»), y otorga a EMPRESARIO (en adelante, «el Servicio») la autorización que "
    "sigue.",

    "1. Objeto. La Empresa autoriza al Servicio a acceder, con las credenciales que ella "
    "misma proporciona (usuario y clave SOL de SUNAT y, de ser el caso, credenciales de "
    "SUNAFIL y AFPnet), a los portales de dichas entidades para CONSULTAR y DESCARGAR, de "
    "forma automatizada y periódica, información de la Empresa: ficha RUC y tributos "
    "afectos, buzón electrónico y notificaciones, comprobantes de pago electrónicos "
    "emitidos y recibidos, recibos por honorarios, perfil de cumplimiento, declaraciones "
    "y pagos, información del ITF, planilla electrónica y casilla electrónica SUNAFIL, "
    "aportes previsionales (AFPnet) y demás información análoga disponible en dichos "
    "portales.",

    "2. Finalidad. La información se obtiene con la única finalidad de mostrarla a la "
    "Empresa y a las personas que ella invite, procesarla para generar indicadores, "
    "alertas, calendarios de vencimientos, estimaciones tributarias y análisis "
    "financieros, y enviarle avisos relacionados. El Servicio NO presenta declaraciones, "
    "NO realiza pagos, NO modifica datos en los portales ni realiza trámite alguno en "
    "nombre de la Empresa.",

    "3. Credenciales. La clave SOL se almacena cifrada y se usa exclusivamente para la "
    "finalidad indicada. El Servicio recomienda conectar un USUARIO SOL SECUNDARIO con "
    "permisos de solo consulta. La Empresa puede revocar el acceso en cualquier momento "
    "desconectando SUNAT desde su perfil, con lo que la clave se elimina; también puede "
    "cambiar o dar de baja el usuario secundario desde SUNAT.",

    "4. Datos personales. En la información consultada pueden figurar datos personales "
    "(representantes, trabajadores, clientes y proveedores). La Empresa declara contar "
    "con base legal para su tratamiento y encarga al Servicio su tratamiento con la "
    "finalidad indicada, conforme a la Ley N.º 29733, Ley de Protección de Datos "
    "Personales, y su Reglamento. El Servicio aplica medidas de seguridad razonables, no "
    "cede la información a terceros ni la usa con fines distintos, y la conserva mientras "
    "la cuenta esté activa o por el plazo que la ley exija.",

    "5. Responsabilidad. La información mostrada proviene de los portales oficiales y se "
    "presenta tal como se obtiene, con los cálculos y estimaciones que el Servicio "
    "declara como referenciales. La Empresa es responsable del cumplimiento de sus "
    "obligaciones tributarias y laborales y de verificar la información antes de tomar "
    "decisiones.",

    "6. Registro de la autorización. El Servicio conservará constancia de esta "
    "autorización: la identidad de quien la otorga, el usuario SOL conectado, la fecha y "
    "hora, la dirección IP y el navegador utilizados, y la versión exacta de este texto.",
]

TEXTO = "\n\n".join([TITULO, f"Versión {VERSION}"] + PARRAFOS)
SHA256 = hashlib.sha256(TEXTO.encode("utf-8")).hexdigest()

# Qué se autoriza, en claves cortas: queda grabado junto a la autorización.
ALCANCES = [
    "ficha_ruc", "buzon", "comprobantes", "honorarios", "perfil_cumplimiento",
    "declaraciones_pagos", "itf", "planilla_sunafil", "afpnet",
]


def documento() -> dict:
    return {
        "version": VERSION, "titulo": TITULO, "parrafos": PARRAFOS,
        "sha256": SHA256, "alcances": ALCANCES,
    }


def client_ip(request) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()[:45]
    return (request.META.get("REMOTE_ADDR") or "")[:45]
