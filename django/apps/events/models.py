"""Models for events.

All models must:
  - inherit from AbstractBaseModel (UUID pk, created_at, updated_at)
  - use Decimal for money fields (Decimal(18,4))
  - never reference models from other apps directly
"""

from libs.errors.base_model import AbstractBaseModel  # noqa: F401

# Add your models here.
