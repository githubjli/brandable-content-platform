"""Tests for Commerce V1-AVS: the ProductOrder purchase chain.

Exercises ProductOrder -> payments.Order -> (wallet debit | Stripe webhook) ->
payments.OrderPaid -> commerce settle, plus stock and cancellation rules.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from decimal import Decimal

import pytest

from apps.commerce import services as commerce
from apps.commerce.models import Product, ProductOrder, SellerStore
from apps.economy import services as economy
from apps.events.dispatcher import dispatch_pending_batch
from apps.payments import services as payments
from libs.errors.exceptions import ConflictError, NotFoundError, UnprocessableError, ValidationError

WEBHOOK_SECRET = "whsec_test_secret"


def _uid() -> str:
    return str(uuid.uuid4())


def _store() -> SellerStore:
    return SellerStore.objects.create(
        owner_user_id=uuid.uuid4(), slug=f"store-{uuid.uuid4().hex[:8]}", name="Test Store"
    )


def _product(store: SellerStore, *, price: str, currency: str, stock: int = 5) -> Product:
    return Product.objects.create(
        store=store,
        title="Widget",
        price_amount=Decimal(price),
        price_currency=currency,
        stock=stock,
    )


def _signed(event: dict) -> tuple[bytes, str]:
    payload = json.dumps(event)
    ts = int(time.time())
    sig = hmac.new(WEBHOOK_SECRET.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return payload.encode(), f"t={ts},v1={sig}"


def _succeeded(intent_id: str, event_id: str = "evt_co") -> dict:
    return {
        "id": event_id,
        "object": "event",
        "type": "payment_intent.succeeded",
        "data": {"object": {"id": intent_id, "object": "payment_intent"}},
    }


# ---------------------------------------------------------------------------
# Wallet payment (MP/MC) — the synchronous-debit path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWalletOrder:
    def test_pays_debits_and_settles(self):
        uid = _uid()
        economy.create_wallets_for_user(user_id=uid)
        economy.credit(
            user_id=uid, currency="MC", entry_type="RECHARGE", amount="500", idempotency_key="seed"
        )
        product = _product(_store(), price="100", currency="MC", stock=3)

        order = commerce.create_order(
            user_id=uid,
            product_id=str(product.id),
            quantity=1,
            payment_provider="wallet",
            payment_asset="MC",
            idempotency_key="ok1",
        )
        assert order["status"] == "pending_payment"
        assert order["amounts"]["subtotal"] == {"amount": "100.0000", "currency": "MC"}
        assert order["amounts"]["platform_fee"]["amount"] == "5.0000"
        assert order["amounts"]["seller_receivable"]["amount"] == "95.0000"
        # Wallet debited immediately.
        assert economy.get_balance(user_id=uid, currency="MC") == Decimal("400.0000")

        # payments.OrderPaid was emitted; dispatch flips the ProductOrder to paid.
        dispatch_pending_batch()
        assert commerce.get_order(order_no=order["order_no"], user_id=uid)["status"] == "paid"
        product.refresh_from_db()
        assert product.stock == 2

    def test_insufficient_balance_rolls_back(self):
        uid = _uid()
        economy.create_wallets_for_user(user_id=uid)  # MC balance 0
        product = _product(_store(), price="100", currency="MC", stock=3)

        with pytest.raises(UnprocessableError) as exc:
            commerce.create_order(
                user_id=uid,
                product_id=str(product.id),
                quantity=1,
                payment_provider="wallet",
                payment_asset="MC",
                idempotency_key="ok1",
            )
        assert exc.value.code == "WALLET_INSUFFICIENT_BALANCE"
        # Everything rolled back: no order, stock untouched.
        assert ProductOrder.objects.count() == 0
        product.refresh_from_db()
        assert product.stock == 3


# ---------------------------------------------------------------------------
# Stripe payment — settles via webhook
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStripeOrder:
    def test_pays_via_webhook(self, settings):
        settings.STRIPE_WEBHOOK_SECRET = WEBHOOK_SECRET
        uid = _uid()
        product = _product(_store(), price="29.99", currency="USD", stock=5)

        order = commerce.create_order(
            user_id=uid,
            product_id=str(product.id),
            quantity=1,
            payment_provider="stripe",
            payment_asset="USD",
            idempotency_key="ok1",
        )
        assert order["status"] == "pending_payment"
        assert order["payment"]["provider"] == "stripe"
        intent_id = order["payment"]["intent_id"]

        payload, sig = _signed(_succeeded(intent_id))
        payments.handle_stripe_webhook(payload=payload, signature=sig)
        dispatch_pending_batch()

        assert commerce.get_order(order_no=order["order_no"], user_id=uid)["status"] == "paid"


# ---------------------------------------------------------------------------
# Validation, stock, cancellation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRules:
    def test_insufficient_stock(self):
        product = _product(_store(), price="10", currency="USD", stock=1)
        with pytest.raises(UnprocessableError) as exc:
            commerce.create_order(
                user_id=_uid(),
                product_id=str(product.id),
                quantity=2,
                payment_provider="stripe",
                payment_asset="USD",
                idempotency_key="k1",
            )
        assert exc.value.code == "STOCK_INSUFFICIENT"
        assert exc.value.detail == {"requested": 2, "available": 1}

    def test_asset_mismatch(self):
        product = _product(_store(), price="10", currency="USD", stock=5)
        with pytest.raises(ValidationError) as exc:
            commerce.create_order(
                user_id=_uid(),
                product_id=str(product.id),
                quantity=1,
                payment_provider="wallet",
                payment_asset="MC",
                idempotency_key="k1",
            )
        assert exc.value.code == "ORDER_ASSET_MISMATCH"

    def test_unknown_product(self):
        with pytest.raises(NotFoundError):
            commerce.create_order(
                user_id=_uid(),
                product_id=str(uuid.uuid4()),
                quantity=1,
                payment_provider="stripe",
                payment_asset="USD",
                idempotency_key="k1",
            )

    def test_create_is_idempotent(self):
        product = _product(_store(), price="10", currency="USD", stock=5)
        uid = _uid()
        a = commerce.create_order(
            user_id=uid,
            product_id=str(product.id),
            quantity=1,
            payment_provider="stripe",
            payment_asset="USD",
            idempotency_key="dup",
        )
        b = commerce.create_order(
            user_id=uid,
            product_id=str(product.id),
            quantity=1,
            payment_provider="stripe",
            payment_asset="USD",
            idempotency_key="dup",
        )
        assert a["order_no"] == b["order_no"]
        assert ProductOrder.objects.count() == 1

    def test_cancel_unpaid_releases_stock(self):
        product = _product(_store(), price="10", currency="USD", stock=5)
        uid = _uid()
        order = commerce.create_order(
            user_id=uid,
            product_id=str(product.id),
            quantity=2,
            payment_provider="stripe",
            payment_asset="USD",
            idempotency_key="k1",
        )
        product.refresh_from_db()
        assert product.stock == 3  # reserved

        cancelled = commerce.cancel_order(
            order_no=order["order_no"], user_id=uid, reason="changed mind"
        )
        assert cancelled["status"] == "cancelled"
        product.refresh_from_db()
        assert product.stock == 5  # released

    def test_cancel_paid_rejected(self):
        uid = _uid()
        economy.create_wallets_for_user(user_id=uid)
        economy.credit(
            user_id=uid, currency="MC", entry_type="RECHARGE", amount="500", idempotency_key="seed"
        )
        product = _product(_store(), price="100", currency="MC", stock=3)
        order = commerce.create_order(
            user_id=uid,
            product_id=str(product.id),
            quantity=1,
            payment_provider="wallet",
            payment_asset="MC",
            idempotency_key="k1",
        )
        dispatch_pending_batch()  # settle -> paid

        with pytest.raises(ConflictError) as exc:
            commerce.cancel_order(order_no=order["order_no"], user_id=uid)
        assert exc.value.code == "ORDER_NOT_CANCELLABLE"
