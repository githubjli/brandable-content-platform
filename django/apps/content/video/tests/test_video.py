"""Tests for content.video V2: public catalog + interactions (content-video.md §1-2)."""

from __future__ import annotations

import uuid

import pytest
from rest_framework.test import APIClient

from apps.content.video.models import Video, VideoCategory, VideoComment, VideoLike, VideoView
from apps.identity.models import User


def _user(display_name: str = "Creator") -> User:
    return User.objects.create(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com", password_hash="x", display_name=display_name
    )


def _client_for(user: User) -> APIClient:
    from libs.jwt_auth.authentication import JWTUser

    client = APIClient()
    client.force_authenticate(
        user=JWTUser({"sub": str(user.id), "type": "access", "jti": str(uuid.uuid4())})
    )
    return client


def _video(owner: User, **over) -> Video:
    kwargs = {
        "owner_user_id": owner.id,
        "title": "Clip",
        "description": "a clip",
        "visibility": Video.PUBLIC,
        "access_type": Video.FREE,
        "duration_seconds": 120,
    }
    kwargs.update(over)
    return Video.objects.create(**kwargs)


@pytest.mark.django_db
class TestCatalog:
    def test_list_only_public_active_with_shape(self):
        owner = _user("Jane")
        cat = VideoCategory.objects.create(name="Music", slug="music")
        _video(owner, title="Public", category=cat)
        _video(owner, title="Private", visibility=Video.PRIVATE)
        _video(owner, title="Inactive", is_active=False)

        resp = APIClient().get("/api/v1/content/video/public")
        assert resp.status_code == 200
        body = resp.json()
        assert {v["title"] for v in body["results"]} == {"Public"}
        item = body["results"][0]
        assert item["owner"]["display_name"] == "Jane"
        assert item["category"]["slug"] == "music"
        assert item["counts"]["like"] == 0
        assert item["viewer_context"]["can_watch"] is True
        assert "cursor" in body

    def test_filter_by_category_and_search(self):
        owner = _user()
        music = VideoCategory.objects.create(name="Music", slug="music")
        _video(owner, title="Guitar solo", category=music)
        _video(owner, title="Cooking", description="pasta")

        by_cat = APIClient().get("/api/v1/content/video/public?category=music")
        assert {v["title"] for v in by_cat.json()["results"]} == {"Guitar solo"}

        by_q = APIClient().get("/api/v1/content/video/public?search=pasta")
        assert {v["title"] for v in by_q.json()["results"]} == {"Cooking"}

    def test_detail_and_not_found(self):
        v = _video(_user())
        ok = APIClient().get(f"/api/v1/content/video/public/{v.id}")
        assert ok.status_code == 200
        assert ok.json()["description_html"] == "a clip"

        missing = APIClient().get(f"/api/v1/content/video/public/{uuid.uuid4()}")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "VIDEO_NOT_FOUND"

    def test_members_only_not_watchable(self):
        v = _video(_user(), access_type=Video.MEMBERS_ONLY)
        resp = APIClient().get(f"/api/v1/content/video/public/{v.id}")
        assert resp.json()["viewer_context"]["can_watch"] is False


@pytest.mark.django_db
class TestLikes:
    def test_like_is_idempotent_and_unlike(self):
        owner = _user()
        v = _video(owner)
        liker = _user()
        client = _client_for(liker)

        first = client.post(f"/api/v1/content/video/public/{v.id}/like")
        assert first.status_code == 200
        assert first.json() == {"video_id": str(v.id), "is_liked": True, "like_count": 1}
        # Liking again is a no-op (count stays 1).
        client.post(f"/api/v1/content/video/public/{v.id}/like")
        assert VideoLike.objects.filter(video=v).count() == 1
        v.refresh_from_db()
        assert v.like_count == 1

        un = client.delete(f"/api/v1/content/video/public/{v.id}/like")
        assert un.json()["like_count"] == 0
        assert VideoLike.objects.filter(video=v).count() == 0

    def test_like_requires_auth(self):
        v = _video(_user())
        assert APIClient().post(f"/api/v1/content/video/public/{v.id}/like").status_code == 401

    def test_viewer_context_is_liked(self):
        v = _video(_user())
        liker = _user()
        client = _client_for(liker)
        client.post(f"/api/v1/content/video/public/{v.id}/like")
        detail = client.get(f"/api/v1/content/video/public/{v.id}")
        assert detail.json()["viewer_context"]["is_liked"] is True
        # Anonymous viewer sees is_liked False.
        anon = APIClient().get(f"/api/v1/content/video/public/{v.id}")
        assert anon.json()["viewer_context"]["is_liked"] is False


@pytest.mark.django_db
class TestComments:
    def test_create_list_and_threading(self):
        v = _video(_user())
        client = _client_for(_user())
        top = client.post(
            f"/api/v1/content/video/public/{v.id}/comments",
            {"content": "first!"},
            format="json",
        )
        assert top.status_code == 201
        top_id = top.json()["id"]
        assert top.json()["parent_id"] is None

        reply = client.post(
            f"/api/v1/content/video/public/{v.id}/comments",
            {"content": "reply", "parent_id": top_id},
            format="json",
        )
        assert reply.status_code == 201
        assert reply.json()["parent_id"] == top_id

        # Top-level list shows only the top comment, with reply_count = 1.
        listed = APIClient().get(f"/api/v1/content/video/public/{v.id}/comments")
        results = listed.json()["results"]
        assert len(results) == 1
        assert results[0]["reply_count"] == 1

        # Replies fetched via ?parent_id.
        replies = APIClient().get(
            f"/api/v1/content/video/public/{v.id}/comments?parent_id={top_id}"
        )
        assert {c["content"] for c in replies.json()["results"]} == {"reply"}

        v.refresh_from_db()
        assert v.comment_count == 2

    def test_invalid_parent_rejected(self):
        v = _video(_user())
        other = _video(_user())
        stray = VideoComment.objects.create(video=other, user_id=uuid.uuid4(), content="x")
        resp = _client_for(_user()).post(
            f"/api/v1/content/video/public/{v.id}/comments",
            {"content": "hi", "parent_id": str(stray.id)},
            format="json",
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "COMMENT_PARENT_INVALID"

    def test_comment_requires_auth(self):
        v = _video(_user())
        resp = APIClient().post(
            f"/api/v1/content/video/public/{v.id}/comments", {"content": "hi"}, format="json"
        )
        assert resp.status_code == 401


@pytest.mark.django_db
class TestShareAndView:
    def test_share_increments_count_anonymously(self):
        v = _video(_user())
        resp = APIClient().post(
            f"/api/v1/content/video/public/{v.id}/share", {"channel": "whatsapp"}, format="json"
        )
        assert resp.status_code == 200
        assert resp.json()["share_count"] == 1

    def test_view_is_deduped_per_minute(self):
        v = _video(_user())
        client = _client_for(_user())
        first = client.post(f"/api/v1/content/video/public/{v.id}/view")
        assert first.json()["view_count"] == 1
        # Same user within the same minute → no extra view.
        client.post(f"/api/v1/content/video/public/{v.id}/view")
        v.refresh_from_db()
        assert v.view_count == 1
        assert VideoView.objects.filter(video=v).count() == 1
