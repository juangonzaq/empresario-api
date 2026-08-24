from django.apps import AppConfig


class BillingConfig(AppConfig):
    name = "billing"
    verbose_name = "suscripciones y pagos"

    def ready(self) -> None:
        from . import signals  # noqa: F401 — engancha la prueba gratuita al alta de empresa
