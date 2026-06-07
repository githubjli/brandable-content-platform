"""Tests for content.drama slice 2: favorites, watch progress, comments, view/share
(content-drama.md §1, §4, §5, §6)."""

from __future__ import annotations

import uuid

import pytest
from rest_framework.test import APIClient

from apps.content.drama.models import (
    DramaEpisode,
    DramaFavorite,
    DramaSeries,
    DramaSeriesView,
    DramaWatchProgress,
)
from apps.identity.models import User


def _user() -> User:
    return User.objects.create(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com", password_hash="x", display_name="U"
    )


def _client_for(uid: str) -> APIClient:
    from libs.jwt_auth.authentication import JWTUser

    client = APIClient()
    client.force_authenticate(
        user=JWTUser({"sub": str(uid), "type": "access", "jti": str(uuid.uuid4())})
    )
    return client


def _series() -> DramaSeries:
    return DramaSeries.objects.create(owner_user_id=_user().id, title="Drama")


def _episode(series: DramaSeries, no: int) -> DramaEpisode:
    return DramaEpisode.objects.create(
        series=series, episode_no=no, title=f"Ep {no}", unlock_type=DramaEpisode.FREE, is_free=True
    )


@pytest.mark.django_db
class TestFavorites:
    def test_favorite_toggle_and_count(self):
        s = _series()
        uid = str(uuid.uuid4())
        client = _client_for(uid)

        fav = client.post(f"/api/v1/content/drama/series/{s.id}/favorite")
        assert fav.status_code == 200
        assert fav.json() == {"series_id": str(s.id), "is_favorited": True, "favorite_count": 1}
        # Idempotent: favoriting again keeps count at 1.
        client.post(f"/api/v1/content/drama/series/{s.id}/favorite")
        assert DramaFavorite.objects.filter(series=s).count() == 1

        # Reflected in viewer_context.
        detail = client.get(f"/api/v1/content/drama/series/{s.id}")
        assert detail.json()["viewer_context"]["is_favorited"] is True

        un = client.delete(f"/api/v1/content/drama/series/{s.id}/favorite")
        assert un.json()["favorite_count"] == 0
        s.refresh_from_db()
        assert s.favorite_count == 0

    def test_favorite_requires_auth(self):
        s = _series()
        assert APIClient().post(f"/api/v1/content/drama/series/{s.id}/favorite").status_code == 401


@pytest.mark.django_db
class TestProgress:
    def test_upsert_and_get_and_continue_card(self):
        s = _series()
        e1 = _episode(s, 1)
        e2 = _episode(s, 2)
        uid = str(uuid.uuid4())
        client = _client_for(uid)

        # No progress yet → 404.
        assert client.get(f"/api/v1/content/drama/series/{s.id}/progress").status_code == 404

        post = client.post(
            f"/api/v1/content/drama/series/{s.id}/progress",
            {"episode_id": str(e1.id), "progress_seconds": 120},
            format="json",
        )
        assert post.status_code == 200
        assert post.json()["progress_seconds"] == 120
        assert post.json()["episode_no"] == 1

        # Upsert to episode 2 (same row per user+series).
        client.post(
            f"/api/v1/content/drama/series/{s.id}/progress",
            {"episode_id": str(e2.id), "progress_seconds": 45, "completed": False},
            format="json",
        )
        assert DramaWatchProgress.objects.filter(user_id=uid, series=s).count() == 1
        latest = client.get(f"/api/v1/content/drama/series/{s.id}/progress").json()
        assert latest["episode_no"] == 2
        assert latest["progress_seconds"] == 45

        # continue card surfaces in the series detail viewer_context.
        cont = client.get(f"/api/v1/content/drama/series/{s.id}").json()["viewer_context"][
            "continue"
        ]
        assert cont == {"episode_no": 2, "progress_seconds": 45}

    def test_episode_scoped_progress(self):
        s = _series()
        e1 = _episode(s, 1)
        uid = str(uuid.uuid4())
        client = _client_for(uid)
        resp = client.post(
            f"/api/v1/content/drama/episodes/{e1.id}/progress",
            {"progress_seconds": 30},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["episode_no"] == 1
        assert resp.json()["progress_seconds"] == 30


@pytest.mark.django_db
class TestComments:
    def test_create_list_threading(self):
        s = _series()
        client = _client_for(str(uuid.uuid4()))
        top = client.post(
            f"/api/v1/content/drama/series/{s.id}/comments", {"content": "love it"}, format="json"
        )
        assert top.status_code == 201
        top_id = top.json()["id"]

        client.post(
            f"/api/v1/content/drama/series/{s.id}/comments",
            {"content": "agree", "parent_id": top_id},
            format="json",
        )
        listed = APIClient().get(f"/api/v1/content/drama/series/{s.id}/comments")
        results = listed.json()["results"]
        assert len(results) == 1
        assert results[0]["reply_count"] == 1

        replies = APIClient().get(
            f"/api/v1/content/drama/series/{s.id}/comments?parent_id={top_id}"
        )
        assert {c["content"] for c in replies.json()["results"]} == {"agree"}

        s.refresh_from_db()
        assert s.comment_count == 2

    def test_comment_requires_auth(self):
        s = _series()
        resp = APIClient().post(
            f"/api/v1/content/drama/series/{s.id}/comments", {"content": "x"}, format="json"
        )
        assert resp.status_code == 401


@pytest.mark.django_db
class TestViewAndShare:
    def test_view_deduped_per_minute(self):
        s = _series()
        client = _client_for(str(uuid.uuid4()))
        first = client.post(f"/api/v1/content/drama/series/{s.id}/view")
        assert first.json()["view_count"] == 1
        client.post(f"/api/v1/content/drama/series/{s.id}/view")
        s.refresh_from_db()
        assert s.view_count == 1
        assert DramaSeriesView.objects.filter(series=s).count() == 1

    def test_share_increments_anonymously(self):
        s = _series()
        resp = APIClient().post(
            f"/api/v1/content/drama/series/{s.id}/share", {"channel": "line"}, format="json"
        )
        assert resp.status_code == 200
        assert resp.json()["share_count"] == 1
