"""Copia en disco de cada comprobante que se scrapea.

Los comprobantes viven en la base (el XML firmado de facturas y notas, el
detalle de cada recibo por honorarios), pero una carpeta ordenada por empresa
y periodo es lo que se entrega al contador, se respalda o se mueve a otro
almacenamiento sin pasar por el API. La ruta, relativa a ``MEDIA_ROOT``::

    comprobantes/<ruc>/<año>/<mes>/<tipo>/<serie-número>-<uuid>.<extensión>

El uuid es la clave primaria de la fila, así que cada comprobante tiene una
sola ruta posible: volver a bajarlo reemplaza el archivo en vez de duplicarlo.
Los modelos declaran un ``FileField`` cuyo ``upload_to`` arma esa ruta, y
:func:`store` es la única forma de escribir en él.
"""

from __future__ import annotations

import re
from typing import Any

from django.core.files.base import ContentFile
from django.db.models.fields.files import FieldFile

ROOT = "comprobantes"

_UNSAFE = re.compile(r"[^A-Za-z0-9-]+")


def document_path(
    *,
    account_ruc: str,
    period: str,
    kind: str,
    code: str,
    pk: Any,
    extension: str,
) -> str:
    """La ruta relativa a ``MEDIA_ROOT`` de un comprobante.

    ``period`` es aaaamm, derivado de la fecha de emisión; un comprobante sin
    fecha cae en ``0000/00`` antes que quedarse sin archivo. ``code`` es
    serie-número y se limpia porque forma parte del nombre del archivo.
    """
    if len(period or "") == 6:
        year, month = period[:4], period[4:6]
    else:
        year, month = "0000", "00"
    code = _UNSAFE.sub("", code or "").strip("-") or "sin-numero"
    extension = extension.lower().lstrip(".")
    return f"{ROOT}/{account_ruc}/{year}/{month}/{kind}/{code}-{pk}.{extension}"


def store(field: FieldFile, content: bytes, extension: str) -> None:
    """Escribe (o reemplaza) el archivo del comprobante; la ruta la decide el
    ``upload_to`` del campo.

    No guarda la fila: quien llama agrupa este cambio con los suyos en un solo
    ``save``. La ruta de destino se libera antes de escribir porque, si el
    almacenamiento encuentra el nombre ocupado, inventa otro con un sufijo
    aleatorio y la carpeta deja de ser predecible.
    """
    name = f"documento.{extension.lstrip('.')}"
    target = field.field.generate_filename(field.instance, name)
    if field and field.name != target:
        # La fila cambió de periodo o de tipo: la copia vieja sobra.
        field.delete(save=False)
    if field.storage.exists(target):
        field.storage.delete(target)
    field.save(name, ContentFile(content), save=False)
