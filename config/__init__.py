"""Expose the Celery app so shared_task picks it up when Django starts."""

from .celery import app as celery_app

__all__ = ("celery_app",)
