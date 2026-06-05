"""Tests for Payments V1: Order lifecycle, adapters, Stripe webhook, recharge E2E."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from decimal import Decimal

import pytest

from apps.economy import services as economy
from apps.events.dispatcher import dispatch_pending_batch
from apps.payments import services
from apps.payments.models import Order, WebhookEvent
from libs.errors.exceptions import ConflictError, NotFoundError, ValidationError

WEBHOOK_SECRET = "whsec_test_secret"


def _uid() -> str:
    return str(uuid.uuid4())


def _create_stripe_order(user_id: str, ref: str | None = None) -> dict:
    return services.create_order(
        user_id=user_id,
        business_kind="CREDIT_RECHARGE",
        business_ref_id=ref or _uid(),
        amount="3.00",
        currency="USD",
        payment_provider="stripe",
        idempotency_key=f"k:{uuid.uuid4().hex}",
    )


def _signed(event: dict) -> tuple[bytes, str]:
    payload = json.dumps(event)
    ts = int(time.time())
    sig = hmac.new(WEBHOOK_SECRET.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return payload.encode(), f"t={ts},v1={sig}"


def _succeeded_event(intent_id: str, event_id: str = "evt_1") -> dict:
    return {
        "id": event_id,
        "object": "event",
        "type": "payment_intent.succeeded",
        "data": {"object": {"id": intent_id, "object": "payment_intent"}},
    }


# ---------------------------------------------------------------------------
# Order creation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateOrder:
    def test_stripe_order_has_intent_and_client_secret(self):
        order = _create_stripe_order(_uid())
        assert order["status"] == "pending_payment"
        assert order["payment"]["provider"] == "stripe"
        assert order["payment"]["intent_id"].startswith("pi_fake_")
        assert order["payment"]["client_secret"]

    def test_blockchain_ltt_order_surfaces_pay_to_address(self, settings):
        settings.LTT_RECEIVE_ADDRESS = "0xLTTRECEIVE"
        order = services.create_order(
            user_id=_uid(),
            business_kind="CREDIT_RECHARGE",
            business_ref_id=_uid(),
            amount="100",
            currency="THB-LTT",
            payment_provider="blockchain",
            blockchain_network="ltt",
            idempotency_key="k1",
        )
        assert order["payment"]["blockchain_network"] == "ltt"
        assert order["payment"]["pay_to_address"] == "0xLTTRECEIVE"
        assert order["payment"]["required_confirmations"] == settings.LTT_REQUIRED_CONFIRMATIONS

    def test_unsupported_network_rejected(self):
        with pytest.raises(ValidationError) as exc:
            services.create_order(
                user_id=_uid(),
                business_kind="CREDIT_RECHARGE",
                business_ref_id=_uid(),
                amount="1",
                currency="LBC",
                payment_provider="blockchain",
                blockchain_network="lbc",
                idempotency_key="k1",
            )
        assert exc.value.code == "BLOCKCHAIN_NETWORK_UNSUPPORTED"

    def test_create_is_idempotent(self):
        uid = _uid()
        a = services.create_order(
            user_id=uid,
            business_kind="PRODUCT",
            business_ref_id=_uid(),
            amount="5",
            currency="USD",
            payment_provider="stripe",
            idempotency_key="same",
        )
        b = services.create_order(
            user_id=uid,
            business_kind="PRODUCT",
            business_ref_id=_uid(),
            amount="5",
            currency="USD",
            payment_provider="stripe",
            idempotency_key="same",
        )
        assert a["order_no"] == b["order_no"]
        assert Order.objects.count() == 1


# ---------------------------------------------------------------------------
# get / cancel / verify
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLifecycle:
    def test_get_owner_only(self):
        uid = _uid()
        order = _create_stripe_order(uid)
        assert services.get_order(order_no=order["order_no"], user_id=uid)["order_no"]
        with pytest.raises(NotFoundError):
            services.get_order(order_no=order["order_no"], user_id=_uid())

    def test_cancel_pending(self):
        uid = _uid()
        order = _create_stripe_order(uid)
        cancelled = services.cancel_order(order_no=order["order_no"], actor_id=uid)
        assert cancelled["status"] == "cancelled"

    def test_cancel_paid_rejected(self):
        uid = _uid()
        order = _create_stripe_order(uid)
        o = Order.objects.get(order_no=order["order_no"])
        services._mark_paid(o, intent_id=o.provider_intent_id)
        with pytest.raises(ConflictError) as exc:
            services.cancel_order(order_no=order["order_no"], actor_id=uid)
        assert exc.value.code == "ORDER_NOT_CANCELLABLE"

    def test_blockchain_verify_stays_pending(self, settings):
        settings.LTT_RECEIVE_ADDRESS = "0xLTT"
        uid = _uid()
        order = services.create_order(
            user_id=uid,
            business_kind="CREDIT_RECHARGE",
            business_ref_id=_uid(),
            amount="100",
            currency="THB-LTT",
            payment_provider="blockchain",
            blockchain_network="ltt",
            idempotency_key="k1",
        )
        result = services.verify_order(order_no=order["order_no"], user_id=uid, txid="0xdeadbeef")
        assert result["status"] == "verifying"
        assert Order.objects.get(order_no=order["order_no"]).status == "pending_payment"


# ---------------------------------------------------------------------------
# Stripe webhook
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStripeWebhook:
    def test_bad_signature_rejected(self, settings):
        settings.STRIPE_WEBHOOK_SECRET = WEBHOOK_SECRET
        with pytest.raises(ValidationError) as exc:
            services.handle_stripe_webhook(payload=b'{"id":"evt"}', signature="t=1,v1=bad")
        assert exc.value.code == "PAYMENT_WEBHOOK_SIGNATURE_INVALID"

    def test_succeeded_marks_order_paid_and_dedups(self, settings):
        settings.STRIPE_WEBHOOK_SECRET = WEBHOOK_SECRET
        order = _create_stripe_order(_uid())
        intent_id = order["payment"]["intent_id"]

        payload, sig = _signed(_succeeded_event(intent_id))
        res = services.handle_stripe_webhook(payload=payload, signature=sig)
        assert res["status"] == "processed"
        assert Order.objects.get(order_no=order["order_no"]).status == "paid"

        # Same event id again → idempotent duplicate.
        res2 = services.handle_stripe_webhook(payload=payload, signature=sig)
        assert res2["status"] == "duplicate"
        assert WebhookEvent.objects.count() == 1


# ---------------------------------------------------------------------------
# End-to-end: economy recharge → payments → Stripe webhook → MC credited
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRechargeChain:
    def test_stripe_recharge_credits_mc(self, settings):
        settings.STRIPE_WEBHOOK_SECRET = WEBHOOK_SECRET
        from apps.economy.models import CreditPackage

        CreditPackage.objects.create(
            code="CREDIT_100",
            name="100",
            credit_amount=Decimal("100"),
            bonus_credit=Decimal("10"),
            price_amount=Decimal("3"),
            price_currency="USD",
            payment_provider="stripe",
        )
        uid = _uid()
        economy.create_wallets_for_user(user_id=uid)

        recharge = economy.create_credit_recharge(
            user_id=uid, package_code="CREDIT_100", idempotency_key="rk1"
        )
        intent_id = recharge["payment"]["intent_id"]
        assert economy.get_balance(user_id=uid, currency="MC") == Decimal("0.0000")

        # Stripe confirms payment via webhook → Order PAID → payments.OrderPaid emitted.
        payload, sig = _signed(_succeeded_event(intent_id))
        services.handle_stripe_webhook(payload=payload, signature=sig)

        # Dispatch runs economy.fulfill_credit_recharge → credits MC (total 110).
        dispatch_pending_batch()
        assert economy.get_balance(user_id=uid, currency="MC") == Decimal("110.0000")
        from apps.economy.models import CreditRecharge

        assert CreditRecharge.objects.get(id=recharge["id"]).status == "completed"
