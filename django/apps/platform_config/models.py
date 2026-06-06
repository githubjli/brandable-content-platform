"""Models for platform_config (platform-config.md).

Single brand-wide configuration row in V1 (per ADR-0001). Read it via
services.get_platform_config(); never construct/query the model directly outside
this app. Secrets (Stripe secret key, JWT private key) are NOT stored here — they
live in the secrets manager / env.
"""

from __future__ import annotations

from django.core.cache import cache
from django.db.models import BooleanField, CharField, EmailField, URLField

from libs.errors.base_model import AbstractBaseModel

CONFIG_CACHE_KEY = "platform_config:singleton"


class PlatformConfig(AbstractBaseModel):
    # Site / branding
    site_name = CharField(max_length=100, default="Brandable Platform")
    tagline = CharField(max_length=200, blank=True, default="")
    logo_url = URLField(blank=True, default="")
    favicon_url = URLField(blank=True, default="")
    primary_color = CharField(max_length=7, default="#000000")  # #RRGGBB
    secondary_color = CharField(max_length=7, default="#FFFFFF")
    support_email = EmailField(blank=True, default="")

    # Client version gating
    min_supported_app_version = CharField(max_length=20, default="1.0.0")
    force_upgrade_below = CharField(max_length=20, blank=True, default="")

    # Feature flags
    live_enabled = BooleanField(default=True)
    drama_enabled = BooleanField(default=True)
    commerce_enabled = BooleanField(default=True)
    membership_enabled = BooleanField(default=True)
    registration_open = BooleanField(default=True)

    # Provider public config (publishable key only — never the secret)
    stripe_publishable_key = CharField(max_length=255, blank=True, default="")

    # Links
    terms_url = URLField(blank=True, default="")
    privacy_url = URLField(blank=True, default="")
    help_url = URLField(blank=True, default="")

    class Meta:
        db_table = "platform_config"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Any write invalidates the read-through cache (services.get_platform_config).
        cache.delete(CONFIG_CACHE_KEY)

    def __str__(self) -> str:
        return f"PlatformConfig({self.site_name})"
