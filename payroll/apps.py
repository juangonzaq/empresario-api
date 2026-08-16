from django.apps import AppConfig


class PayrollConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "payroll"
    verbose_name = "Planilla"

    def ready(self) -> None:
        from . import signals  # noqa: F401  (salary history tracking)
