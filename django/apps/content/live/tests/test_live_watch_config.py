"""Tests for content.live watch-config (content-live.md §1)."""

from __future__ import annotations

import uuid

import pytest
from rest_framework.test import APIClient

from apps.content.live.models import LiveStream, LiveStreamView
from apps.identity.models import User


def _user() -> User:
    return User.objects.create(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com", password_hash="x", display_name="U"
    )


def _client(uid: str) -> APIClient:
    from libs.jwt_auth.authentication import JWTUser

    client = APIClient()
    client.force_authenticate(
        user=JWTUser({"sub": str(uid), "type": "access", "jti": str(uuid.uuid4())})
    )
    return client


def _stream(owner: User, *, status: str = LiveStream.LIVE, **over) -> LiveStream:
    return LiveStream.objects.create(
        owner_user_id=owner.id, title="S", status=status, ant_media_stream_id="ams_xyz", **over
    )


@pytest.mark.django_db
class TestWatchConfig:
    def test_playback_shape_when_live(self):
        s = _stream(_user(), thumbnail_url="https://t/x.jpg")
        resp = APIClient().get(f"/api/v1/content/live/streams/{s.id}/watch-config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["live_id"] == str(s.id)
        assert body["status"] == "live"
        assert body["playback"]["mode"] == "webrtc"
        assert body["playback"]["stream_id"] == "ams_xyz"
        assert body["playback"]["connected"] is True
        assert body["fallback"]["mode"] == "hls"
        assert body["thumbnail_url"] == "https://t/x.jpg"

    def test_viewer_count_dedup_per_minute(self):
        s = _stream(_user())
        viewer = _client(str(_user().id))
        first = viewer.get(f"/api/v1/content/live/streams/{s.id}/watch-config")
        assert first.json()["viewer_count"] == 1
        # Same viewer within the minute → no extra count.
        viewer.get(f"/api/v1/content/live/streams/{s.id}/watch-config")
        s.refresh_from_db()
        assert s.viewer_count == 1
        assert LiveStreamView.objects.filter(stream=s).count() == 1

    def test_distinct_viewers_each_count(self):
        s = _stream(_user())
        _client(str(_user().id)).get(f"/api/v1/content/live/streams/{s.id}/watch-config")
        _client(str(_user().id)).get(f"/api/v1/content/live/streams/{s.id}/watch-config")
        s.refresh_from_db()
        assert s.viewer_count == 2

    def test_not_live_stream_still_returns_config(self):
        s = _stream(_user(), status=LiveStream.IDLE)
        resp = APIClient().get(f"/api/v1/content/live/streams/{s.id}/watch-config")
        assert resp.status_code == 200
        assert resp.json()["playback"]["connected"] is False

    def test_private_stream_404_for_non_owner(self):
        owner = _user()
        s = _stream(owner, visibility=LiveStream.PRIVATE)
        assert (
            APIClient().get(f"/api/v1/content/live/streams/{s.id}/watch-config").status_code == 404
        )
        assert (
            _client(str(owner.id))
            .get(f"/api/v1/content/live/streams/{s.id}/watch-config")
            .status_code
            == 200
        )
