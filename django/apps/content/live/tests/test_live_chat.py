"""Tests for content.live chat (content-live.md §2)."""

from __future__ import annotations

import uuid

import pytest
from rest_framework.test import APIClient

from apps.content.live.models import LiveChatMessage, LiveStream
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
    return LiveStream.objects.create(owner_user_id=owner.id, title="S", status=status, **over)


def _post(client: APIClient, stream_id, content="hi") -> dict:
    return client.post(
        f"/api/v1/content/live/streams/{stream_id}/chat/messages",
        {"content": content},
        format="json",
    ).json()


@pytest.mark.django_db
class TestPost:
    def test_post_message_when_live(self):
        owner = _user()
        s = _stream(owner)
        client = _client(str(_user().id))
        resp = client.post(
            f"/api/v1/content/live/streams/{s.id}/chat/messages",
            {"content": "first!"},
            format="json",
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["type"] == "text"
        assert body["content"] == "first!"
        assert body["live_id"] == str(s.id)

    def test_post_to_non_live_rejected(self):
        owner = _user()
        s = _stream(owner, status=LiveStream.ENDED)
        resp = _client(str(_user().id)).post(
            f"/api/v1/content/live/streams/{s.id}/chat/messages",
            {"content": "x"},
            format="json",
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "LIVE_STREAM_NOT_LIVE"

    def test_post_requires_auth(self):
        s = _stream(_user())
        resp = APIClient().post(
            f"/api/v1/content/live/streams/{s.id}/chat/messages", {"content": "x"}, format="json"
        )
        assert resp.status_code == 401

    def test_product_message_type(self):
        owner = _user()
        s = _stream(owner)
        pid = str(uuid.uuid4())
        resp = _client(str(_user().id)).post(
            f"/api/v1/content/live/streams/{s.id}/chat/messages",
            {"content": "", "product_id": pid},
            format="json",
        )
        assert resp.status_code == 201
        assert resp.json()["type"] == "product"
        assert resp.json()["product"] == {"id": pid}


@pytest.mark.django_db
class TestHistory:
    def test_list_and_after_id(self):
        owner = _user()
        s = _stream(owner)
        sender = _client(str(_user().id))
        m1 = _post(sender, s.id, "one")
        m2 = _post(sender, s.id, "two")

        listed = APIClient().get(f"/api/v1/content/live/streams/{s.id}/chat/messages")
        assert [m["content"] for m in listed.json()["results"]] == ["one", "two"]
        assert listed.json()["next_after_id"] == m2["id"]

        after = APIClient().get(
            f"/api/v1/content/live/streams/{s.id}/chat/messages?after_id={m1['id']}"
        )
        assert [m["content"] for m in after.json()["results"]] == ["two"]

    def test_deleted_messages_excluded(self):
        owner = _user()
        s = _stream(owner)
        author = _user()
        msg = _post(_client(str(author.id)), s.id, "bye")
        _client(str(author.id)).delete(
            f"/api/v1/content/live/streams/{s.id}/chat/messages/{msg['id']}"
        )
        listed = APIClient().get(f"/api/v1/content/live/streams/{s.id}/chat/messages")
        assert listed.json()["results"] == []


@pytest.mark.django_db
class TestModeration:
    def test_author_can_delete_own(self):
        owner = _user()
        s = _stream(owner)
        author = _user()
        msg = _post(_client(str(author.id)), s.id)
        resp = _client(str(author.id)).delete(
            f"/api/v1/content/live/streams/{s.id}/chat/messages/{msg['id']}"
        )
        assert resp.status_code == 204
        assert LiveChatMessage.objects.get(id=msg["id"]).is_active is False

    def test_broadcaster_can_delete_any(self):
        owner = _user()
        s = _stream(owner)
        msg = _post(_client(str(_user().id)), s.id)
        resp = _client(str(owner.id)).delete(
            f"/api/v1/content/live/streams/{s.id}/chat/messages/{msg['id']}"
        )
        assert resp.status_code == 204

    def test_other_viewer_cannot_delete(self):
        owner = _user()
        s = _stream(owner)
        msg = _post(_client(str(_user().id)), s.id)
        resp = _client(str(_user().id)).delete(
            f"/api/v1/content/live/streams/{s.id}/chat/messages/{msg['id']}"
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "CHAT_DELETE_FORBIDDEN"

    def test_pin_broadcaster_only(self):
        owner = _user()
        s = _stream(owner)
        author = _user()
        msg = _post(_client(str(author.id)), s.id)

        # Author cannot pin.
        bad = _client(str(author.id)).put(
            f"/api/v1/content/live/streams/{s.id}/chat/messages/{msg['id']}/pin"
        )
        assert bad.status_code == 403
        assert bad.json()["error"]["code"] == "CHAT_PIN_FORBIDDEN"

        # Broadcaster can pin.
        ok = _client(str(owner.id)).put(
            f"/api/v1/content/live/streams/{s.id}/chat/messages/{msg['id']}/pin"
        )
        assert ok.status_code == 200
        assert ok.json()["is_pinned"] is True
