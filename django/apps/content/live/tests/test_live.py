"""Tests for content.live V3 slice 1: stream lifecycle + browse (content-live.md §1, §5, §7)."""

from __future__ import annotations

import uuid

import pytest
from rest_framework.test import APIClient

from apps.content.live.models import LiveCategory, LiveStream
from apps.identity.models import User


def _user(display_name: str = "Creator", *, creator: bool = True) -> User:
    return User.objects.create(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        display_name=display_name,
        is_creator=creator,
    )


def _client(uid: str) -> APIClient:
    from libs.jwt_auth.authentication import JWTUser

    client = APIClient()
    client.force_authenticate(
        user=JWTUser({"sub": str(uid), "type": "access", "jti": str(uuid.uuid4())})
    )
    return client


def _create(client: APIClient, **over) -> dict:
    payload = {"title": "My Stream", "visibility": "public"}
    payload.update(over)
    return client.post(
        "/api/v1/content/live/me/streams",
        payload,
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    ).json()


@pytest.mark.django_db
class TestBrowse:
    def test_list_only_public_with_shape(self):
        owner = _user("Jane")
        cat = LiveCategory.objects.create(name="Gaming", slug="gaming")
        LiveStream.objects.create(
            owner_user_id=owner.id, title="Pub", category=cat, status=LiveStream.LIVE
        )
        LiveStream.objects.create(
            owner_user_id=owner.id, title="Priv", visibility=LiveStream.PRIVATE
        )

        resp = APIClient().get("/api/v1/content/live/streams")
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert {s["title"] for s in results} == {"Pub"}
        item = results[0]
        assert item["owner"]["display_name"] == "Jane"
        assert item["category"]["slug"] == "gaming"
        assert item["effective_status"] == "live"
        assert "broadcaster_config" not in item

    def test_filter_by_status(self):
        owner = _user()
        LiveStream.objects.create(owner_user_id=owner.id, title="L", status=LiveStream.LIVE)
        LiveStream.objects.create(owner_user_id=owner.id, title="I", status=LiveStream.IDLE)
        resp = APIClient().get("/api/v1/content/live/streams?status=live")
        assert {s["title"] for s in resp.json()["results"]} == {"L"}

    def test_detail_hides_broadcaster_config_from_non_owner(self):
        owner = _user()
        s = LiveStream.objects.create(
            owner_user_id=owner.id, title="S", stream_key="secret", status=LiveStream.LIVE
        )
        anon = APIClient().get(f"/api/v1/content/live/streams/{s.id}")
        assert "broadcaster_config" not in anon.json()
        # Owner sees broadcaster_config.
        owner_view = _client(str(owner.id)).get(f"/api/v1/content/live/streams/{s.id}")
        assert owner_view.json()["broadcaster_config"]["stream_key"] == "secret"

    def test_private_stream_404_for_non_owner(self):
        owner = _user()
        s = LiveStream.objects.create(
            owner_user_id=owner.id, title="S", visibility=LiveStream.PRIVATE
        )
        assert APIClient().get(f"/api/v1/content/live/streams/{s.id}").status_code == 404
        assert _client(str(owner.id)).get(f"/api/v1/content/live/streams/{s.id}").status_code == 200


@pytest.mark.django_db
class TestLifecycle:
    def test_create_requires_creator(self):
        resp = _client(str(_user(creator=False).id)).post(
            "/api/v1/content/live/me/streams",
            {"title": "X"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "NOT_CREATOR"

    def test_full_state_machine(self):
        owner = _user()
        client = _client(str(owner.id))
        created = _create(client)
        sid = created["id"]
        assert created["status"] == "idle"
        # Runtime (fake mode) provisioned credentials.
        assert created["broadcaster_config"]["stream_key"].startswith("key_ams_")

        prepared = client.post(
            f"/api/v1/content/live/me/streams/{sid}/prepare", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())
        )
        assert prepared.json()["status"] == "ready"

        started = client.post(
            f"/api/v1/content/live/me/streams/{sid}/start", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())
        )
        assert started.json()["ok"] is True
        assert started.json()["status"] == "live"
        assert started.json()["already_started"] is False

        # Re-start is idempotent.
        again = client.post(
            f"/api/v1/content/live/me/streams/{sid}/start", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())
        )
        assert again.json()["already_started"] is True

        ended = client.post(
            f"/api/v1/content/live/me/streams/{sid}/end", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())
        )
        assert ended.json()["status"] == "ended"

        st = LiveStream.objects.get(id=sid)
        assert st.started_at is not None
        assert st.ended_at is not None

    def test_cannot_start_ended_stream(self):
        owner = _user()
        client = _client(str(owner.id))
        sid = _create(client)["id"]
        client.post(
            f"/api/v1/content/live/me/streams/{sid}/end", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())
        )
        resp = client.post(
            f"/api/v1/content/live/me/streams/{sid}/start", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "LIVE_INVALID_STATE"

    def test_status_endpoint(self):
        owner = _user()
        client = _client(str(owner.id))
        sid = _create(client)["id"]
        st = client.get(f"/api/v1/content/live/streams/{sid}/status").json()
        assert st["can_start"] is True
        assert st["can_end"] is False
        assert st["status"] == "idle"

    def test_my_streams_and_ownership_scoping(self):
        owner = _user()
        sid = _create(_client(str(owner.id)))["id"]
        assert (
            len(_client(str(owner.id)).get("/api/v1/content/live/me/streams").json()["results"])
            == 1
        )
        # Another creator cannot prepare it.
        intruder = _user()
        resp = _client(str(intruder.id)).post(
            f"/api/v1/content/live/me/streams/{sid}/prepare", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())
        )
        assert resp.status_code == 404

    def test_update_metadata(self):
        owner = _user()
        client = _client(str(owner.id))
        sid = _create(client)["id"]
        resp = client.patch(
            f"/api/v1/content/live/me/streams/{sid}",
            {"title": "Renamed", "visibility": "private"},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Renamed"
        assert resp.json()["visibility"] == "private"

    def test_create_requires_auth(self):
        assert APIClient().get("/api/v1/content/live/me/streams").status_code == 401
