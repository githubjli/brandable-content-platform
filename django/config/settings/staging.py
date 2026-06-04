"""Staging settings — mirrors production but with relaxed logging."""

import os

from .base import *  # noqa: F403

DEBUG = False

# DATABASE_URL, SECRET_KEY, etc. come from the environment (set by Ansible/systemd)

_staging_host = os.environ.get("STAGING_HOST", "staging.example.com")
ALLOWED_HOSTS = [_staging_host, f"www.{_staging_host}"]

# OTel always on in staging
OTEL_ENABLED = True

# Slightly more verbose logging on staging for debugging
LOGGING["root"]["level"] = "DEBUG"  # noqa: F405
