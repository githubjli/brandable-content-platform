"""Tests for content.drama V2: catalog + 4-method episode unlock (content-drama.md §1-3, §9)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.content.drama.models import DramaEpisode, DramaSeries, DramaUnlock
from apps.economy import services as economy
from apps.identity.models import User


def _user(display_name: str = "Creator") -> User:
    return User.objects.create(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com", password_hash="x", display_name=display_name
    )


def _client_for(uid: str) -> APIClient:
    from libs.jwt_auth.authentication import JWTUser

    client = APIClient()
    client.force_authenticate(
        user=JWTUser({"sub": str(uid), "type": "access", "jti": str(uuid.uuid4())})
    )
    return client


def _series(owner: User, **over) -> DramaSeries:
    kwargs = {
        "owner_user_id": owner.id,
        "title": "My Drama",
        "total_episodes": 0,
        "free_episodes": 0,
    }
    kwargs.update(over)
    return DramaSeries.objects.create(**kwargs)


def _episode(
    series: DramaSeries, no: int, *, unlock_type=DramaEpisode.FREE, **over
) -> DramaEpisode:
    kwargs = {
        "series": series,
        "episode_no": no,
        "title": f"Ep {no}",
        "unlock_type": unlock_type,
        "is_free": unlock_type == DramaEpisode.FREE,
        "playback_url": "https://cdn/ep.m3u8",
    }
    kwargs.update(over)
    return DramaEpisode.objects.create(**kwargs)


@pytest.mark.django_db
class TestCatalog:
    def test_series_list_and_detail(self):
        owner = _user("Jane")
        _series(owner, title="A", total_episodes=12, free_episodes=3)
        _series(owner, title="Hidden", is_active=False)

        resp = APIClient().get("/api/v1/content/drama/series")
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert {s["title"] for s in results} == {"A"}
        item = results[0]
        assert item["owner"]["display_name"] == "Jane"
        assert item["counts"] == {
            "total_episodes": 12,
            "free_episodes": 3,
            "locked_episodes": 9,
            "view": 0,
            "favorite": 0,
            "comment": 0,
            "share": 0,
            "gift_amount": "0.0000",
            "gift_currency": "MP",
        }

    def test_series_not_found(self):
        resp = APIClient().get(f"/api/v1/content/drama/series/{uuid.uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "SERIES_NOT_FOUND"


@pytest.mark.django_db
class TestEpisodeAccess:
    def test_episode_list_access_flags(self):
        owner = _user()
        s = _series(owner)
        _episode(s, 1, unlock_type=DramaEpisode.FREE)
        _episode(s, 2, unlock_type=DramaEpisode.MEOW_POINTS, points_price=Decimal("10"))
        _episode(s, 3, unlock_type=DramaEpisode.MEMBERSHIP)

        resp = APIClient().get(f"/api/v1/content/drama/series/{s.id}/episodes")
        eps = {e["episode_no"]: e for e in resp.json()["episodes"]}
        assert eps[1]["viewer_context"] == {
            "is_unlocked": True,
            "can_watch": True,
            "unlocked_via": "free",
        }
        assert eps[2]["viewer_context"]["is_unlocked"] is False
        assert eps[2]["pricing"]["points_price"] == "10.0000"
        # Anonymous viewer has no membership → membership episode locked.
        assert eps[3]["viewer_context"]["can_watch"] is False

    def test_episode_detail_playback_hidden_when_locked(self):
        owner = _user()
        s = _series(owner)
        _episode(s, 1, unlock_type=DramaEpisode.FREE)
        _episode(s, 2, unlock_type=DramaEpisode.MEOW_POINTS, points_price=Decimal("5"))

        free = APIClient().get(f"/api/v1/content/drama/series/{s.id}/episodes/1")
        assert free.json()["playback"]["playback_url"] == "https://cdn/ep.m3u8"
        assert free.json()["navigation"]["next_episode_no"] == 2

        locked = APIClient().get(f"/api/v1/content/drama/series/{s.id}/episodes/2")
        assert locked.json()["playback"] is None
        assert locked.json()["navigation"]["previous_episode_no"] == 1


@pytest.mark.django_db
class TestUnlock:
    def _funded(self, currency: str, amount: str = "100") -> tuple[str, APIClient]:
        uid = str(uuid.uuid4())
        economy.create_wallets_for_user(user_id=uid)
        entry_type = "PURCHASE" if currency == "MP" else "RECHARGE"
        economy.credit(
            user_id=uid,
            currency=currency,
            entry_type=entry_type,
            amount=amount,
            idempotency_key=f"seed-{uid}",
        )
        return uid, _client_for(uid)

    def test_unlock_with_meow_points_debits_and_grants(self):
        owner = _user()
        s = _series(owner)
        ep = _episode(s, 1, unlock_type=DramaEpisode.MEOW_POINTS, points_price=Decimal("10"))
        uid, client = self._funded("MP")

        resp = client.post(
            f"/api/v1/content/drama/episodes/{ep.id}/unlock",
            {"payment_method": "meow_points"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_unlocked"] is True
        assert body["points_charged"] == "10.0000"
        assert body["currency"] == "MP"
        assert body["ledger_entry_id"] is not None
        assert economy.get_balance(user_id=uid, currency="MP") == Decimal("90.0000")
        assert DramaUnlock.objects.filter(user_id=uid, episode=ep).count() == 1

        # Episode now shows unlocked for this viewer.
        detail = client.get(f"/api/v1/content/drama/series/{s.id}/episodes/1")
        assert detail.json()["viewer_context"]["is_unlocked"] is True
        assert detail.json()["playback"] is not None

    def test_unlock_is_idempotent(self):
        owner = _user()
        s = _series(owner)
        ep = _episode(s, 1, unlock_type=DramaEpisode.MEOW_CREDIT, credits_price=Decimal("2"))
        uid, client = self._funded("MC")
        for _ in range(2):
            resp = client.post(
                f"/api/v1/content/drama/episodes/{ep.id}/unlock",
                {"payment_method": "meow_credit"},
                format="json",
                HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
            )
        assert resp.json()["code"] == "ALREADY_UNLOCKED"
        # Charged only once.
        assert economy.get_balance(user_id=uid, currency="MC") == Decimal("98.0000")
        assert DramaUnlock.objects.filter(user_id=uid, episode=ep).count() == 1

    def test_unlock_free_episode_rejected(self):
        owner = _user()
        s = _series(owner)
        ep = _episode(s, 1, unlock_type=DramaEpisode.FREE)
        _uid, client = self._funded("MP")
        resp = client.post(
            f"/api/v1/content/drama/episodes/{ep.id}/unlock",
            {"payment_method": "meow_points"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "DRAMA_FREE_EPISODE"

    def test_unlock_insufficient_balance(self):
        owner = _user()
        s = _series(owner)
        ep = _episode(s, 1, unlock_type=DramaEpisode.MEOW_POINTS, points_price=Decimal("50"))
        uid = str(uuid.uuid4())
        economy.create_wallets_for_user(user_id=uid)  # MP balance 0
        resp = _client_for(uid).post(
            f"/api/v1/content/drama/episodes/{ep.id}/unlock",
            {"payment_method": "meow_points"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "WALLET_INSUFFICIENT_BALANCE"
        assert DramaUnlock.objects.filter(episode=ep).count() == 0

    def test_unlock_requires_auth(self):
        owner = _user()
        s = _series(owner)
        ep = _episode(s, 1, unlock_type=DramaEpisode.MEOW_POINTS, points_price=Decimal("1"))
        resp = APIClient().post(
            f"/api/v1/content/drama/episodes/{ep.id}/unlock",
            {"payment_method": "meow_points"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 401


@pytest.mark.django_db
class TestMembershipAccess:
    def test_member_can_watch_membership_episode(self, monkeypatch):
        owner = _user()
        s = _series(owner)
        _episode(s, 1, unlock_type=DramaEpisode.MEMBERSHIP)
        member = str(uuid.uuid4())

        from apps.content.drama import services as drama

        monkeypatch.setattr(drama, "_has_membership", lambda uid: str(uid) == member)

        resp = _client_for(member).get(f"/api/v1/content/drama/series/{s.id}/episodes")
        ep = resp.json()["episodes"][0]
        assert ep["viewer_context"] == {
            "is_unlocked": True,
            "can_watch": True,
            "unlocked_via": "membership",
        }
