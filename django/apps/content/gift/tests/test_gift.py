"""Tests for content.gift V2: catalog + cross-content gift send + history (gift.md)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.content.drama.models import DramaSeries
from apps.content.gift.models import GiftCatalogItem, GiftTransaction
from apps.content.video.models import Video
from apps.economy import services as economy
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


def _funded(currency: str, amount: str = "5000") -> str:
    uid = str(uuid.uuid4())
    economy.create_wallets_for_user(user_id=uid)
    entry = "PURCHASE" if currency == "MP" else "RECHARGE"
    economy.credit(
        user_id=uid,
        currency=currency,
        entry_type=entry,
        amount=amount,
        idempotency_key=f"seed-{uid}",
    )
    return uid


def _video(owner: User) -> Video:
    return Video.objects.create(owner_user_id=owner.id, title="Clip", visibility=Video.PUBLIC)


def _series(owner: User) -> DramaSeries:
    return DramaSeries.objects.create(owner_user_id=owner.id, title="Drama")


@pytest.mark.django_db
class TestCatalog:
    def test_catalog_lists_active(self):
        GiftCatalogItem.objects.create(
            code="rose", name="Rose", emoji="🌹", preset_amount=Decimal("100"), sort_order=1
        )
        GiftCatalogItem.objects.create(code="hidden", name="x", is_active=False)
        resp = APIClient().get("/api/v1/gifts/catalog")
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert {g["code"] for g in results} == {"rose"}
        assert results[0]["preset_amount"] == "100.0000"


@pytest.mark.django_db
class TestSend:
    def test_send_video_gift_debits_sender_credits_owner(self):
        owner = _user()
        video = _video(owner)
        sender_uid = _funded("MP", "5000")
        client = _client_for(sender_uid)
        # Receiver needs a wallet to be credited.
        economy.create_wallets_for_user(user_id=str(owner.id))

        resp = client.post(
            f"/api/v1/content/video/public/{video.id}/gifts/send",
            {
                "amount": "100",
                "currency": "MP",
                "payment_method": "meow_points",
                "gift_code": "rose",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["transaction"]["target"] == {"type": "video", "id": str(video.id)}
        assert body["transaction"]["receiver_id"] == str(owner.id)
        assert body["sender_balance"] == {"currency": "MP", "amount": "4900.0000"}
        assert body["receiver_balance"] == {"currency": "MP", "amount": "100.0000"}
        assert economy.get_balance(user_id=sender_uid, currency="MP") == Decimal("4900.0000")
        assert economy.get_balance(user_id=str(owner.id), currency="MP") == Decimal("100.0000")

    def test_send_drama_gift_with_credit_wallet(self):
        owner = _user()
        series = _series(owner)
        economy.create_wallets_for_user(user_id=str(owner.id))
        sender_uid = _funded("MC", "500")
        resp = _client_for(sender_uid).post(
            f"/api/v1/content/drama/series/{series.id}/gifts/send",
            {"amount": "30", "currency": "MC", "payment_method": "meow_credit"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 201
        assert resp.json()["transaction"]["target"]["type"] == "drama_series"
        assert economy.get_balance(user_id=str(owner.id), currency="MC") == Decimal("30.0000")

    def test_self_send_forbidden(self):
        owner = _user()
        video = _video(owner)
        economy.create_wallets_for_user(user_id=str(owner.id))
        economy.credit(
            user_id=str(owner.id),
            currency="MP",
            entry_type="PURCHASE",
            amount="500",
            idempotency_key=f"seed-{owner.id}",
        )
        resp = _client_for(str(owner.id)).post(
            f"/api/v1/content/video/public/{video.id}/gifts/send",
            {"amount": "10", "currency": "MP", "payment_method": "meow_points"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "GIFT_SELF_SEND_FORBIDDEN"

    def test_insufficient_balance(self):
        owner = _user()
        video = _video(owner)
        economy.create_wallets_for_user(user_id=str(owner.id))
        sender_uid = str(uuid.uuid4())
        economy.create_wallets_for_user(user_id=sender_uid)  # 0 MP
        resp = _client_for(sender_uid).post(
            f"/api/v1/content/video/public/{video.id}/gifts/send",
            {"amount": "100", "currency": "MP", "payment_method": "meow_points"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "WALLET_INSUFFICIENT_BALANCE"
        assert GiftTransaction.objects.count() == 0

    def test_currency_mismatch_rejected(self):
        owner = _user()
        video = _video(owner)
        sender_uid = _funded("MP")
        resp = _client_for(sender_uid).post(
            f"/api/v1/content/video/public/{video.id}/gifts/send",
            {"amount": "10", "currency": "MC", "payment_method": "meow_points"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "GIFT_CURRENCY_MISMATCH"

    def test_target_not_found(self):
        sender_uid = _funded("MP")
        resp = _client_for(sender_uid).post(
            f"/api/v1/content/video/public/{uuid.uuid4()}/gifts/send",
            {"amount": "10", "currency": "MP", "payment_method": "meow_points"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "TARGET_NOT_FOUND"

    def test_send_requires_auth(self):
        owner = _user()
        video = _video(owner)
        resp = APIClient().post(
            f"/api/v1/content/video/public/{video.id}/gifts/send",
            {"amount": "10", "currency": "MP", "payment_method": "meow_points"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 401


@pytest.mark.django_db
class TestHistory:
    def test_sent_and_received_lists(self):
        owner = _user()
        video = _video(owner)
        economy.create_wallets_for_user(user_id=str(owner.id))
        sender_uid = _funded("MP")
        _client_for(sender_uid).post(
            f"/api/v1/content/video/public/{video.id}/gifts/send",
            {"amount": "10", "currency": "MP", "payment_method": "meow_points"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        sent = _client_for(sender_uid).get("/api/v1/gifts/sent")
        assert len(sent.json()["results"]) == 1
        received = _client_for(str(owner.id)).get("/api/v1/gifts/received")
        assert len(received.json()["results"]) == 1
        assert received.json()["results"][0]["amount"] == "10.0000"
