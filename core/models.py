"""Abstract base models shared across the project."""

from __future__ import annotations

import uuid

from django.db import models


def default_uuid() -> uuid.UUID:
    """Return a UUIDv7 when the runtime supports it, otherwise a UUIDv4.

    UUIDv7 is time-ordered, so using it as a primary key keeps inserts at the right
    edge of the B-tree instead of scattering them the way UUIDv4 does. It still
    carries 74 random bits, far more than enough to keep ids unguessable when they
    are exposed in API URLs. ``uuid.uuid7`` landed in Python 3.14; the fallback keeps
    the project importable on older interpreters.
    """
    generator = getattr(uuid, "uuid7", uuid.uuid4)
    return generator()


class UUIDPrimaryKeyModel(models.Model):
    """Replaces the sequential integer primary key with an opaque UUID."""

    id = models.UUIDField(primary_key=True, default=default_uuid, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    """Tracks when a row was first stored and last written."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BaseModel(UUIDPrimaryKeyModel, TimeStampedModel):
    """Default base for concrete models: UUID primary key plus timestamps."""

    class Meta:
        abstract = True
