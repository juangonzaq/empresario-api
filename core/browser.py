"""Entorno con el que se lanzan los navegadores de los scrapers.

Chrome hereda el entorno de quien lo lanza, y eso incluye las variables
``DYLD_*`` de macOS. Si el shell exporta ``DYLD_LIBRARY_PATH`` apuntando a
Homebrew —algo habitual para compilar extensiones nativas—, Chrome resuelve
``libpng``, ``libjpeg`` y ``libtiff`` a las copias de Homebrew en lugar de a
las del sistema. ImageIO, que es de Apple y espera las suyas, salta entonces a
un puntero incompatible y el proceso muere con SIGBUS nada más arrancar,
antes de abrir la primera pestaña.

El síntoma no se parece en nada a la causa: Playwright informa «Target page,
context or browser has been closed», que se lee como un problema de red o del
portal. Por eso se limpia aquí y no en la configuración de cada máquina: el
scraper no debe depender de cómo tenga el shell quien arrancó el worker.
"""

from __future__ import annotations

import os

# Todas inyectan o reordenan la carga de librerías dinámicas. Ninguna tiene
# nada que hacer dentro del navegador.
PREFIJOS_EXCLUIDOS = ("DYLD_", "LD_PRELOAD", "LD_LIBRARY_PATH")


def browser_env() -> dict[str, str]:
    """El entorno actual sin las variables que inyectan librerías."""
    return {
        clave: valor
        for clave, valor in os.environ.items()
        if not clave.startswith(PREFIJOS_EXCLUIDOS)
    }
