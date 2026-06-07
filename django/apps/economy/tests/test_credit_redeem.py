"""Tests for economy credit redeem (admin workflow) — economy.md §7."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.economy import services
from apps.economy.models import CreditRedeemRequest


def _client(uid: str, *, admin: bool = False) -> APIClient:
    from libs.jwt_auth.authentication import JWTUser

    claims: dict = {"sub": str(uid), "type": "access", "jti": str(uuid.uuid4())}
    if admin:
        claims["scope"] = ["admin"]
    client = APIClient()
    client.force_authenticate(user=JWTUser(claims))
    return client


def _admin() -> APIClient:
    return _client(str(uuid.uuid4()), admin=True)


def _funded(amount: str = "500") -> str:
    uid = str(uuid.uuid4())
    services.create_wallets_for_user(user_id=uid)
    services.credit(
        user_id=uid,
        currency="MC",
        entry_type="RECHARGE",
        amount=amount,
        idempotency_key=f"seed-{uid}",
    )
    return uid


def _request(client: APIClient, **over) -> dict:
    payload = {
        "amount": "100",
        "redeem_method": "blockchain_transfer",
        "blockchain_network": "lbc",
        "account_snapshot": {"address": "bC123"},
    }
    payload.update(over)
    return client.post(
        "/api/v1/economy/credit-redeems",
        payload,
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )


@pytest.mark.django_db
class TestRequest:
    def test_request_holds_funds(self):
        uid = _funded("500")
        resp = _request(_client(uid))
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "requested"
        assert body["amount"] == {"amount": "100.0000", "currency": "MC"}
        # Funds held out of the wallet immediately.
        assert services.get_balance(user_id=uid, currency="MC") == Decimal("400.0000")
        assert CreditRedeemRequest.objects.filter(user_id=uid).count() == 1

    def test_insufficient_balance(self):
        uid = str(uuid.uuid4())
        services.create_wallets_for_user(user_id=uid)  # 0 MC
        resp = _request(_client(uid), amount="100")
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "WALLET_INSUFFICIENT_BALANCE"
        assert CreditRedeemRequest.objects.filter(user_id=uid).count() == 0

    def test_list_own_requests(self):
        uid = _funded("500")
        client = _client(uid)
        _request(client)
        listed = client.get("/api/v1/economy/credit-redeems")
        assert len(listed.json()["results"]) == 1

    def test_requires_auth(self):
        assert APIClient().get("/api/v1/economy/credit-redeems").status_code == 401


@pytest.mark.django_db
class TestAdminReview:
    def test_approve_then_complete(self):
        uid = _funded("500")
        rid = _request(_client(uid)).json()["id"]
        admin = _admin()

        approved = admin.post(
            f"/api/v1/economy/credit-redeems/{rid}/approve",
            {"admin_note": "ok"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"

        completed = admin.post(
            f"/api/v1/economy/credit-redeems/{rid}/complete",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"
        # Funds stay held out (paid out externally).
        assert services.get_balance(user_id=uid, currency="MC") == Decimal("400.0000")

    def test_reject_refunds_held_amount(self):
        uid = _funded("500")
        rid = _request(_client(uid)).json()["id"]  # balance now 400
        resp = _admin().post(
            f"/api/v1/economy/credit-redeems/{rid}/reject",
            {"admin_note": "bad address"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        # Held amount refunded → back to 500.
        assert services.get_balance(user_id=uid, currency="MC") == Decimal("500.0000")

    def test_complete_requires_approved(self):
        uid = _funded("500")
        rid = _request(_client(uid)).json()["id"]
        resp = _admin().post(
            f"/api/v1/economy/credit-redeems/{rid}/complete",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "REDEEM_NOT_APPROVED"

    def test_approve_requires_admin(self):
        uid = _funded("500")
        rid = _request(_client(uid)).json()["id"]
        resp = _client(uid).post(
            f"/api/v1/economy/credit-redeems/{rid}/approve",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 403
