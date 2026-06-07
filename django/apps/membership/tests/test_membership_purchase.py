"""Tests for Membership V2: plans list, current membership, one-shot purchase
(membership.md V2). Wallet purchase settles + grants via PAYMENTS_ORDER_PAID."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.economy import services as economy
from apps.events.dispatcher import dispatch_pending_batch
from apps.membership import services
from apps.membership.models import MembershipOrder, MembershipPlan, UserMembership


def _client_for(uid: str) -> APIClient:
    from libs.jwt_auth.authentication import JWTUser

    client = APIClient()
    client.force_authenticate(
        user=JWTUser({"sub": str(uid), "type": "access", "jti": str(uuid.uuid4())})
    )
    return client


def _plan(*, code="PRO", currency="MC", amount="30", days=30) -> MembershipPlan:
    return MembershipPlan.objects.create(
        code=code,
        name="Pro",
        duration_days=days,
        price_amount=Decimal(amount),
        price_currency=currency,
    )


@pytest.mark.django_db
class TestPlansAndMe:
    def test_plans_list_public(self):
        _plan(code="A", currency="USD", amount="9.99")
        MembershipPlan.objects.create(code="HIDDEN", name="x", is_active=False)
        resp = APIClient().get("/api/v1/membership/plans")
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert {p["code"] for p in results} == {"A"}
        assert results[0]["price"] == {"amount": "9.9900", "currency": "USD"}

    def test_me_requires_auth(self):
        assert APIClient().get("/api/v1/membership/me").status_code == 401

    def test_me_none_then_active(self):
        uid = str(uuid.uuid4())
        client = _client_for(uid)
        assert client.get("/api/v1/membership/me").json() == {"active_membership": None}

        plan = _plan()
        services.grant_membership(user_id=uid, plan_code=plan.code, idempotency_key=f"g-{uid}")
        body = client.get("/api/v1/membership/me").json()
        assert body["status"] == "active"
        assert body["plan"]["code"] == "PRO"


@pytest.mark.django_db
class TestPurchase:
    def test_wallet_purchase_settles_and_grants(self):
        uid = str(uuid.uuid4())
        plan = _plan(currency="MC", amount="30")
        economy.create_wallets_for_user(user_id=uid)
        economy.credit(
            user_id=uid,
            currency="MC",
            entry_type="RECHARGE",
            amount="100",
            idempotency_key=f"seed-{uid}",
        )
        client = _client_for(uid)

        resp = client.post(
            "/api/v1/membership/orders",
            {"plan_id": str(plan.id), "payment_provider": "wallet", "payment_asset": "MC"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "pending_payment"
        assert resp.json()["amount"] == {"amount": "30.0000", "currency": "MC"}
        # Wallet debited immediately.
        assert economy.get_balance(user_id=uid, currency="MC") == Decimal("70.0000")
        # No membership until the OrderPaid event is dispatched.
        assert services.get_active_membership(user_id=uid) is None

        dispatch_pending_batch()

        membership = services.get_active_membership(user_id=uid)
        assert membership is not None
        assert membership.plan.code == "PRO"
        order = MembershipOrder.objects.get(user_id=uid)
        assert order.status == "paid"

    def test_insufficient_balance_rolls_back(self):
        uid = str(uuid.uuid4())
        plan = _plan(currency="MC", amount="30")
        economy.create_wallets_for_user(user_id=uid)  # 0 balance
        resp = _client_for(uid).post(
            "/api/v1/membership/orders",
            {"plan_id": str(plan.id), "payment_provider": "wallet", "payment_asset": "MC"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "WALLET_INSUFFICIENT_BALANCE"
        assert MembershipOrder.objects.filter(user_id=uid).count() == 0
        assert UserMembership.objects.filter(user_id=uid).count() == 0

    def test_stripe_order_pending_no_grant_yet(self):
        # Test settings run Stripe in fake mode (no live key) → synthetic intent.
        uid = str(uuid.uuid4())
        plan = _plan(currency="USD", amount="9.99")
        resp = _client_for(uid).post(
            "/api/v1/membership/orders",
            {"plan_id": str(plan.id), "payment_provider": "stripe", "payment_asset": "USD"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "pending_payment"
        assert services.get_active_membership(user_id=uid) is None

    def test_asset_mismatch_rejected(self):
        uid = str(uuid.uuid4())
        plan = _plan(currency="USD", amount="9.99")
        resp = _client_for(uid).post(
            "/api/v1/membership/orders",
            {"plan_id": str(plan.id), "payment_provider": "wallet", "payment_asset": "MC"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "ORDER_ASSET_MISMATCH"

    def test_order_idempotent(self):
        uid = str(uuid.uuid4())
        plan = _plan(currency="MC", amount="10")
        economy.create_wallets_for_user(user_id=uid)
        economy.credit(
            user_id=uid,
            currency="MC",
            entry_type="RECHARGE",
            amount="100",
            idempotency_key=f"seed-{uid}",
        )
        client = _client_for(uid)
        key = str(uuid.uuid4())
        a = client.post(
            "/api/v1/membership/orders",
            {"plan_id": str(plan.id), "payment_provider": "wallet", "payment_asset": "MC"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )
        b = client.post(
            "/api/v1/membership/orders",
            {"plan_id": str(plan.id), "payment_provider": "wallet", "payment_asset": "MC"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )
        assert a.json()["order_no"] == b.json()["order_no"]
        assert MembershipOrder.objects.filter(user_id=uid).count() == 1
        # Charged once.
        assert economy.get_balance(user_id=uid, currency="MC") == Decimal("90.0000")
