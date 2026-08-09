from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'core'

    def ready(self):
        # Registra las comprobaciones de arranque (claves, caché, hosts).
        from . import checks  # noqa: F401
