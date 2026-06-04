"""Shared ORM base classes."""

import uuid

from django.db import models


class AbstractBaseModel(models.Model):
    """All ORM models inherit from this.

    - UUID v4 primary key (no auto-increment integers per ADR-0001).
    - created_at / updated_at managed automatically.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
