"""HTTP-level tests for economy views (request → view → service → response)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.economy import services
from apps.economy.models import CreditPackage


def _authed_client() -> tuple[APIClient, str]:
    from libs.jwt_auth.authentication import JWTUser

    user_id = str(uuid.uuid4())
    client = APIClient()
    client.force_authenticate(
        user=JWTUser({"sub": user_id, "type": "access", "jti": str(uuid.uuid4())})
    )
    return client, user_id


@pytest.mark.django_db
class TestWalletViews:
    def test_point_wallet_requires_auth(self):
        resp = APIClient().get("/api/v1/economy/wallets/me/point")
        assert resp.status_code == 401

    def test_get_point_wallet(self):
        client, uid = _authed_client()
        services.create_wallets_for_user(user_id=uid)
        resp = client.get("/api/v1/economy/wallets/me/point")
        assert resp.status_code == 200
        assert resp.json()["currency"] == "MP"
        assert resp.json()["balance"] == "0.0000"

    def test_aggregate_balance(self):
        client, uid = _authed_client()
        services.create_wallets_for_user(user_id=uid)
        services.credit(
            user_id=uid, currency="MC", entry_type="RECHARGE", amount="5", idempotency_key="k1"
        )
        resp = client.get("/api/v1/economy/wallets/me")
        assert resp.status_code == 200
        by = {b["currency"]: b["amount"] for b in resp.json()["balances"]}
        assert by == {"MP": "0.0000", "MC": "5.0000"}


@pytest.mark.django_db
class TestLedgerView:
    def test_ledger_paginated(self):
        client, uid = _authed_client()
        services.create_wallets_for_user(user_id=uid)
        services.credit(
            user_id=uid, currency="MP", entry_type="REWARD", amount="100", idempotency_key="k1"
        )
        resp = client.get("/api/v1/economy/wallets/me/point/ledger")
        assert resp.status_code == 200
        body = resp.json()
        assert "results" in body
        assert "cursor" in body
        assert body["results"][0]["entry_type"] == "REWARD"


@pytest.mark.django_db
class TestPackagesView:
    def test_lists_active_packages_only(self):
        client, _ = _authed_client()
        CreditPackage.objects.create(
            code="CREDIT_100",
            name="100",
            credit_amount=Decimal("100"),
            price_amount=Decimal("100"),
            price_currency="LBC",
        )
        CreditPackage.objects.create(
            code="HIDDEN",
            name="hidden",
            credit_amount=Decimal("1"),
            price_amount=Decimal("1"),
            price_currency="LBC",
            is_active=False,
        )
        resp = client.get("/api/v1/economy/credit-packages")
        assert resp.status_code == 200
        codes = [p["code"] for p in resp.json()["results"]]
        assert codes == ["CREDIT_100"]


@pytest.mark.django_db
class TestDailyRewardViews:
    def test_claim_requires_idempotency_key(self):
        client, uid = _authed_client()
        services.create_wallets_for_user(user_id=uid)
        resp = client.post("/api/v1/economy/daily-rewards/claim", {}, format="json")
        assert resp.status_code == 400

    def test_claim_grants(self):
        client, uid = _authed_client()
        services.create_wallets_for_user(user_id=uid)
        resp = client.post(
            "/api/v1/economy/daily-rewards/claim",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="claim-1",
        )
        assert resp.status_code == 200
        assert resp.json()["granted"] is True
        assert services.get_balance(user_id=uid, currency="MP") == Decimal("10.0000")

    def test_status(self):
        client, uid = _authed_client()
        services.create_wallets_for_user(user_id=uid)
        resp = client.get("/api/v1/economy/daily-rewards/status")
        assert resp.status_code == 200
        assert resp.json()["eligible_now"] is True
