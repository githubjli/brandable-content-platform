"""Test settings — fast, isolated, no external services."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key-not-for-production")

from .base import *  # noqa: F403

DEBUG = False

# In-memory SQLite is fast; use postgres in CI via DATABASE_URL env var.
_test_db_url = os.environ.get(
    "DATABASE_URL",
    "postgres://brandable:brandable@localhost:5432/brandable_test",
)

import dj_database_url as _dj  # noqa: E402

DATABASES = {
    "default": _dj.parse(_test_db_url, conn_max_age=0),
}

# Use a dummy cache so tests don't need Redis
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# Disable OTel in tests
OTEL_ENABLED = False

# Faster password hashing
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# django-migration-linter: register as an app so `manage.py lintmigrations` is available
INSTALLED_APPS = [*INSTALLED_APPS, "django_migration_linter"]  # noqa: F405

# These migrations make destructive/locking schema changes on existing tables
# (drop the replaced Product.is_active column; add NOT-NULL columns + indexes).
# The linter flags them for zero-downtime deploys, but the platform is pre-launch
# with no deployed database, so they are safe to apply outright. Exempt them by
# name — the gate stays fully active for every other migration.
MIGRATION_LINTER_OPTIONS = {
    "ignore_name": [
        "0002_shop_catalog",
        "0005_shipment_state_machine",
        "0002_email_verification",
        "0003_two_factor",
    ],
}

# Suppress logging noise in tests
LOGGING["root"]["level"] = "CRITICAL"  # type: ignore[index]  # noqa: F405

# Celery runs tasks eagerly (synchronously) in tests
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
