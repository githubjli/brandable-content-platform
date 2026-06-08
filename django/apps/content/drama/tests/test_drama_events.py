"""Drama emits canonical event_type constants from apps.events.types (events.md §15:
no inline event strings). Guards against the constants drifting from the strings
the engagement actions actually emit."""

from __future__ import annotations

import uuid

import pytest
from rest_framework.test import APIClient

from apps.content.drama.models import DramaEpisode, DramaSeries
from apps.events import types
from apps.events.models import OutboxEvent
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


def _series() -> DramaSeries:
    return DramaSeries.objects.create(owner_user_id=_user().id, title="Drama")


def _emitted(event_type: str) -> bool:
    return OutboxEvent.objects.filter(event_type=event_type).exists()


def test_drama_constants_have_canonical_string_values():
    # The constants must keep their wire format (`content.<PastTense>`); changing a
    # value is a breaking event-contract change, so pin them explicitly.
    assert types.CONTENT_DRAMA_EPISODE_UNLOCKED == "content.DramaEpisodeUnlocked"
    assert types.CONTENT_DRAMA_SERIES_FAVORITED == "content.DramaSeriesFavorited"
    assert types.CONTENT_DRAMA_SERIES_UNFAVORITED == "content.DramaSeriesUnfavorited"
    assert types.CONTENT_DRAMA_PROGRESS_UPDATED == "content.DramaProgressUpdated"
    assert types.CONTENT_DRAMA_SERIES_COMMENTED == "content.DramaSeriesCommented"
    assert types.CONTENT_DRAMA_SERIES_VIEWED == "content.DramaSeriesViewed"
    assert types.CONTENT_DRAMA_SERIES_SHARED == "content.DramaSeriesShared"


@pytest.mark.django_db
class TestDramaEmitsConstants:
    def test_favorite_and_unfavorite(self):
        s = _series()
        c = _client(str(uuid.uuid4()))
        c.post(f"/api/v1/content/drama/series/{s.id}/favorite")
        assert _emitted(types.CONTENT_DRAMA_SERIES_FAVORITED)
        c.delete(f"/api/v1/content/drama/series/{s.id}/favorite")
        assert _emitted(types.CONTENT_DRAMA_SERIES_UNFAVORITED)

    def test_comment_view_share(self):
        s = _series()
        c = _client(str(uuid.uuid4()))
        c.post(
            f"/api/v1/content/drama/series/{s.id}/comments",
            {"content": "nice"},
            format="json",
        )
        assert _emitted(types.CONTENT_DRAMA_SERIES_COMMENTED)
        c.post(f"/api/v1/content/drama/series/{s.id}/view")
        assert _emitted(types.CONTENT_DRAMA_SERIES_VIEWED)
        c.post(f"/api/v1/content/drama/series/{s.id}/share")
        assert _emitted(types.CONTENT_DRAMA_SERIES_SHARED)

    def test_progress(self):
        s = _series()
        ep = DramaEpisode.objects.create(
            series=s, episode_no=1, title="Ep 1", unlock_type=DramaEpisode.FREE, is_free=True
        )
        c = _client(str(uuid.uuid4()))
        c.post(
            f"/api/v1/content/drama/series/{s.id}/progress",
            {"episode_id": str(ep.id), "progress_seconds": 12},
            format="json",
        )
        assert _emitted(types.CONTENT_DRAMA_PROGRESS_UPDATED)
