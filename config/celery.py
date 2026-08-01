"""Celery application for the project.

Schedules live in the database (``django-celery-beat``), so they can be edited from
the admin without a redeploy. :func:`suppliers.tasks` and :func:`sunat_mailbox.tasks`
hold the actual work.
"""

from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("salud_empresarial")

# All Celery settings live in Django settings under the CELERY_ prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self) -> str:
    """Smoke test that the worker is alive and wired to the right settings."""
    return f"ok from {self.request.hostname}"
