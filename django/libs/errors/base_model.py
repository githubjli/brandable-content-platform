"""AbstractBaseModel — UUID pk, created_at, updated_at."""

import uuid

from django.db import models


class AbstractBaseModel(models.Model):
    """All ORM models should inherit from this.

    - UUID v4 primary key (no auto-increment integers).
    - created_at / updated_at managed automatically.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
