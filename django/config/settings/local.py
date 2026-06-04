"""Local development settings."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "local-dev-secret-key-not-for-production")

from .base import *  # noqa: F403

DEBUG = True

ALLOWED_HOSTS = ["*"]

CORS_ALLOW_ALL_ORIGINS = True

# Use local postgres from docker-compose
DATABASES["default"]["NAME"] = os.environ.get("POSTGRES_DB", "brandable")  # noqa: F405
DATABASES["default"]["USER"] = os.environ.get("POSTGRES_USER", "brandable")  # noqa: F405
DATABASES["default"]["PASSWORD"] = os.environ.get("POSTGRES_PASSWORD", "brandable")  # noqa: F405
DATABASES["default"]["HOST"] = os.environ.get("POSTGRES_HOST", "localhost")  # noqa: F405
DATABASES["default"]["PORT"] = os.environ.get("POSTGRES_PORT", "5432")  # noqa: F405

# Show SQL in terminal for debugging
# LOGGING["loggers"]["django.db.backends"] = {"handlers": ["console"], "level": "DEBUG"}

# Django debug toolbar (optional, install separately)
INTERNAL_IPS = ["127.0.0.1"]

# Disable OTel by default locally (opt-in via env var)
OTEL_ENABLED = os.environ.get("OTEL_ENABLED", "false").lower() == "true"
