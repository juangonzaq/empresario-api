"""Avance dentro de un paso de sincronización.

Un paso largo (leer 300 mensajes del buzón, traer meses de comprobantes)
se veía como «Trayendo…» durante minutos sin decir cuánto faltaba. Aquí hay
un canal mínimo: el paso llama a :func:`report_progress` cuando puede, y
``_run_source`` lo anota en el trabajo para que el frontend pinte una barra
por paso. Fuera de una sincronización (comando de gestión, tests) no hay
receptor y la llamada no hace nada.

Devuelve ``False`` cuando el trabajo fue cancelado: el paso que lo consulte
puede parar entre un ítem y el siguiente en vez de terminar todo lo suyo.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable
from contextlib import contextmanager

Reporter = Callable[[int, int, str], bool]

_reporter: contextvars.ContextVar[Reporter | None] = contextvars.ContextVar(
    "sync_progress_reporter", default=None,
)


def report_progress(done: int, total: int, detail: str = "") -> bool:
    """«Voy ``done`` de ``total``». True = sigue; False = te cancelaron."""
    reporter = _reporter.get()
    if reporter is None:
        return True
    return reporter(done, total, detail)


@contextmanager
def progress_scope(reporter: Reporter):
    token = _reporter.set(reporter)
    try:
        yield
    finally:
        _reporter.reset(token)
