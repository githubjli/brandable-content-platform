"""Tests for PlatformConfig (platform-config.md): public/admin config, cache, events."""

from __future__ import annotations

import uuid

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.events.models import OutboxEvent
from apps.platform_config import services


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _admin_client() -> APIClient:
    from libs.jwt_auth.authentication import JWTUser

    client = APIClient()
    client.force_authenticate(
        user=JWTUser({"sub": str(uuid.uuid4()), "scope": ["default", "admin"], "type": "access"})
    )
    return client


@pytest.mark.django_db
class TestPublicConfig:
    def test_no_auth_returns_config_with_cache_header(self):
        resp = APIClient().get("/api/v1/platform/config")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) >= {"site", "client", "features", "providers", "links", "generated_at"}
        assert body["site"]["name"] == "Brandable Platform"  # default singleton
        assert body["features"]["registration_open"] is True
        assert resp["Cache-Control"] == "public, max-age=300"


@pytest.mark.django_db
class TestAdminConfig:
    def test_requires_admin(self):
        assert APIClient().get("/api/v1/admin/platform/config").status_code == 401

    def test_patch_updates_and_emits_event_and_audit(self):
        client = _admin_client()
        resp = client.patch(
            "/api/v1/admin/platform/config",
            {"site_name": "Acme", "primary_color": "#FF6B35"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="cfg-1",
        )
        assert resp.status_code == 200
        assert resp.json()["site"]["name"] == "Acme"
        assert resp.json()["site"]["primary_color"] == "#FF6B35"

        assert OutboxEvent.objects.filter(event_type="platform.ConfigUpdated").exists()
        assert AuditLog.objects.filter(action="platform.config.update").exists()

    def test_patch_feature_flag_emits_feature_toggled(self):
        client = _admin_client()
        client.patch(
            "/api/v1/admin/platform/config",
            {"registration_open": False},
            format="json",
            HTTP_IDEMPOTENCY_KEY="cfg-1",
        )
        ev = OutboxEvent.objects.filter(event_type="platform.FeatureToggled").first()
        assert ev is not None
        assert ev.payload["flag"] == "registration_open"
        assert ev.payload["enabled"] is False

    def test_patch_invalid_color_rejected(self):
        resp = _admin_client().patch(
            "/api/v1/admin/platform/config",
            {"primary_color": "red"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="cfg-1",
        )
        assert resp.status_code == 400

    def test_patch_invalid_stripe_key_rejected(self):
        resp = _admin_client().patch(
            "/api/v1/admin/platform/config",
            {"stripe_publishable_key": "sk_live_oops"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="cfg-1",
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestCache:
    def test_update_invalidates_cache(self):
        assert services.get_platform_config().site_name == "Brandable Platform"  # primes cache
        services.update_config(changes={"site_name": "Renamed"}, actor_id=str(uuid.uuid4()))
        # Cache was invalidated by save(); next read reflects the change.
        assert services.get_platform_config().site_name == "Renamed"
