"""Tests for content.live gift send (content-live.md §4) — reuses apps.content.gift."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.content.gift.models import GiftTransaction
from apps.content.live.models import LiveStream
from apps.economy import services as economy
from apps.events.dispatcher import dispatch_pending_batch
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


def _funded(amount: str = "5000") -> str:
    uid = str(uuid.uuid4())
    economy.create_wallets_for_user(user_id=uid)
    economy.credit(
        user_id=uid,
        currency="MP",
        entry_type="PURCHASE",
        amount=amount,
        idempotency_key=f"seed-{uid}",
    )
    return uid


def _stream(owner: User, *, status: str = LiveStream.LIVE) -> LiveStream:
    return LiveStream.objects.create(owner_user_id=owner.id, title="S", status=status)


@pytest.mark.django_db
class TestLiveGift:
    def test_send_debits_credits_and_broadcasts(self):
        owner = _user()
        economy.create_wallets_for_user(user_id=str(owner.id))
        s = _stream(owner)
        sender = _funded("5000")

        resp = _client(sender).post(
            f"/api/v1/content/live/streams/{s.id}/gifts/send",
            {"amount": "100", "currency": "MP", "payment_method": "meow_points"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["transaction"]["target"] == {"type": "live_stream", "id": str(s.id)}
        assert body["transaction"]["receiver_id"] == str(owner.id)
        # Live gifts carry a broadcast event block.
        assert body["event"]["type"] == "gift_event"
        assert body["event"]["broadcast_status"] == "queued"
        assert economy.get_balance(user_id=sender, currency="MP") == Decimal("4900.0000")
        assert economy.get_balance(user_id=str(owner.id), currency="MP") == Decimal("100.0000")

        # The broadcast event was emitted to the outbox.
        assert OutboxEvent.objects.filter(event_type="content.live.GiftSent").exists()
        dispatch_pending_batch()

    def test_gift_to_non_live_stream_rejected(self):
        owner = _user()
        economy.create_wallets_for_user(user_id=str(owner.id))
        s = _stream(owner, status=LiveStream.ENDED)
        sender = _funded()
        resp = _client(sender).post(
            f"/api/v1/content/live/streams/{s.id}/gifts/send",
            {"amount": "10", "currency": "MP", "payment_method": "meow_points"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "LIVE_STREAM_NOT_LIVE"
        assert GiftTransaction.objects.count() == 0

    def test_self_gift_forbidden(self):
        owner = _user()
        economy.create_wallets_for_user(user_id=str(owner.id))
        economy.credit(
            user_id=str(owner.id),
            currency="MP",
            entry_type="PURCHASE",
            amount="500",
            idempotency_key=f"seed-{owner.id}",
        )
        s = _stream(owner)
        resp = _client(str(owner.id)).post(
            f"/api/v1/content/live/streams/{s.id}/gifts/send",
            {"amount": "10", "currency": "MP", "payment_method": "meow_points"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "GIFT_SELF_SEND_FORBIDDEN"

    def test_requires_auth(self):
        s = _stream(_user())
        resp = APIClient().post(
            f"/api/v1/content/live/streams/{s.id}/gifts/send",
            {"amount": "10", "currency": "MP", "payment_method": "meow_points"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 401
