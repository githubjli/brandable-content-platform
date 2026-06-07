"""Tests for content.live per-stream payment methods (content-live.md §6)."""

from __future__ import annotations

import uuid

import pytest
from rest_framework.test import APIClient

from apps.content.live.models import LiveStream, LiveStreamPaymentMethod
from apps.identity.models import User


def _user(*, creator: bool = True) -> User:
    return User.objects.create(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        display_name="U",
        is_creator=creator,
    )


def _client(uid: str) -> APIClient:
    from libs.jwt_auth.authentication import JWTUser

    client = APIClient()
    client.force_authenticate(
        user=JWTUser({"sub": str(uid), "type": "access", "jti": str(uuid.uuid4())})
    )
    return client


def _stream(owner: User) -> LiveStream:
    return LiveStream.objects.create(owner_user_id=owner.id, title="S")


@pytest.mark.django_db
class TestPaymentMethods:
    def test_default_empty(self):
        owner = _user()
        s = _stream(owner)
        resp = _client(str(owner.id)).get(f"/api/v1/content/live/me/streams/{s.id}/payment-methods")
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_set_enables_in_order(self):
        owner = _user()
        s = _stream(owner)
        resp = _client(str(owner.id)).put(
            f"/api/v1/content/live/me/streams/{s.id}/payment-methods",
            {"methods": ["stripe", "meow_points"]},
            format="json",
        )
        assert resp.status_code == 200
        enabled = [m for m in resp.json()["results"] if m["is_enabled"]]
        assert [m["method"] for m in sorted(enabled, key=lambda m: m["sort_order"])] == [
            "stripe",
            "meow_points",
        ]

    def test_replace_all_disables_unlisted(self):
        owner = _user()
        s = _stream(owner)
        c = _client(str(owner.id))
        url = f"/api/v1/content/live/me/streams/{s.id}/payment-methods"
        c.put(url, {"methods": ["stripe", "meow_points"]}, format="json")
        c.put(url, {"methods": ["blockchain"]}, format="json")
        results = {m["method"]: m["is_enabled"] for m in c.get(url).json()["results"]}
        assert results["blockchain"] is True
        assert results["stripe"] is False
        assert results["meow_points"] is False

    def test_empty_disables_all(self):
        owner = _user()
        s = _stream(owner)
        c = _client(str(owner.id))
        url = f"/api/v1/content/live/me/streams/{s.id}/payment-methods"
        c.put(url, {"methods": ["stripe"]}, format="json")
        c.put(url, {"methods": []}, format="json")
        assert all(not m["is_enabled"] for m in c.get(url).json()["results"])

    def test_invalid_method_rejected(self):
        owner = _user()
        s = _stream(owner)
        resp = _client(str(owner.id)).put(
            f"/api/v1/content/live/me/streams/{s.id}/payment-methods",
            {"methods": ["paypal"]},
            format="json",
        )
        # Serializer ChoiceField rejects unknown → 400 validation error.
        assert resp.status_code == 400

    def test_requires_ownership(self):
        owner = _user()
        other = _user()
        s = _stream(owner)
        resp = _client(str(other.id)).get(f"/api/v1/content/live/me/streams/{s.id}/payment-methods")
        assert resp.status_code == 404

    def test_idempotent_repeated_set(self):
        owner = _user()
        s = _stream(owner)
        c = _client(str(owner.id))
        url = f"/api/v1/content/live/me/streams/{s.id}/payment-methods"
        c.put(url, {"methods": ["stripe"]}, format="json")
        c.put(url, {"methods": ["stripe"]}, format="json")
        # One row per (stream, method) — no duplicates.
        assert LiveStreamPaymentMethod.objects.filter(stream=s, method="stripe").count() == 1
