"""Serializers for platform_config (admin PATCH input validation)."""

from __future__ import annotations

from rest_framework import serializers

_HEX_COLOR = r"^#[0-9A-Fa-f]{6}$"


class ConfigUpdateSerializer(serializers.Serializer):
    site_name = serializers.CharField(max_length=100, required=False)
    tagline = serializers.CharField(max_length=200, required=False, allow_blank=True)
    logo_url = serializers.URLField(required=False, allow_blank=True)
    favicon_url = serializers.URLField(required=False, allow_blank=True)
    primary_color = serializers.RegexField(_HEX_COLOR, required=False)
    secondary_color = serializers.RegexField(_HEX_COLOR, required=False)
    support_email = serializers.EmailField(required=False, allow_blank=True)
    min_supported_app_version = serializers.CharField(max_length=20, required=False)
    force_upgrade_below = serializers.CharField(max_length=20, required=False, allow_blank=True)
    live_enabled = serializers.BooleanField(required=False)
    drama_enabled = serializers.BooleanField(required=False)
    commerce_enabled = serializers.BooleanField(required=False)
    membership_enabled = serializers.BooleanField(required=False)
    registration_open = serializers.BooleanField(required=False)
    stripe_publishable_key = serializers.CharField(max_length=255, required=False, allow_blank=True)
    terms_url = serializers.URLField(required=False, allow_blank=True)
    privacy_url = serializers.URLField(required=False, allow_blank=True)
    help_url = serializers.URLField(required=False, allow_blank=True)

    def validate_stripe_publishable_key(self, value: str) -> str:
        if value and not value.startswith("pk_"):
            raise serializers.ValidationError("Stripe publishable key must start with 'pk_'.")
        return value
