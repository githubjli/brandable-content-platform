"""Tests for Week 20 / V2 commerce: refunds, QR resolution, public storefront,
and blockchain product payment (commerce.md §3, §4, §9, §12).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.commerce import services as commerce
from apps.commerce.models import Product, ProductOrder, SellerStore
from apps.economy import services as economy
from apps.events.dispatcher import dispatch_pending_batch
from apps.identity.models import User


def _client_for_uid(uid: str, *, admin: bool = False) -> APIClient:
    from libs.jwt_auth.authentication import JWTUser

    claims: dict[str, object] = {"sub": str(uid), "type": "access", "jti": str(uuid.uuid4())}
    if admin:
        claims["scope"] = ["admin"]
    client = APIClient()
    client.force_authenticate(user=JWTUser(claims))
    return client


def _admin_client() -> APIClient:
    return _client_for_uid(str(uuid.uuid4()), admin=True)


def _store(*, slug: str | None = None, active: bool = True) -> SellerStore:
    owner = User.objects.create(
        email=f"o-{uuid.uuid4().hex[:8]}@example.com", password_hash="x", display_name="Owner"
    )
    return SellerStore.objects.create(
        owner_user_id=owner.id,
        slug=slug or f"store-{uuid.uuid4().hex[:8]}",
        name="Store",
        is_active=active,
    )


def _product(store: SellerStore, *, currency: str = "MC", price: str = "10", **over) -> Product:
    kwargs = {
        "store": store,
        "title": "Widget",
        "price_amount": Decimal(price),
        "price_currency": currency,
        "stock": 5,
        "status": Product.ACTIVE,
    }
    kwargs.update(over)
    return Product.objects.create(**kwargs)


def _paid_wallet_order(buyer_uid: str, *, price: str = "10") -> str:
    """Create a digital product, pay via MC wallet, settle to paid. Returns order_no."""
    product = _product(_store(), currency="MC", price=price)
    economy.create_wallets_for_user(user_id=buyer_uid)
    economy.credit(
        user_id=buyer_uid,
        currency="MC",
        entry_type="RECHARGE",
        amount="100",
        idempotency_key=f"seed-{buyer_uid}",
    )
    order = commerce.create_order(
        user_id=buyer_uid,
        product_id=str(product.id),
        quantity=1,
        payment_provider="wallet",
        payment_asset="MC",
        idempotency_key=f"ord-{buyer_uid}",
    )
    dispatch_pending_batch()
    assert ProductOrder.objects.get(order_no=order["order_no"]).status == "paid"
    return order["order_no"]


def _pending_order(store: SellerStore) -> ProductOrder:
    return ProductOrder.objects.create(
        order_no=f"CO-{uuid.uuid4().hex[:10].upper()}",
        buyer_user_id=uuid.uuid4(),
        product=_product(store),
        store=store,
        product_snapshot={"title": "Widget", "cover_image_url": "https://x/y.jpg"},
        quantity=1,
        currency="USD",
        subtotal=Decimal("29.99"),
        platform_fee=Decimal("1.50"),
        seller_receivable=Decimal("28.49"),
        payment_provider="stripe",
        payment_asset="USD",
        status=ProductOrder.PENDING_PAYMENT,
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=30),
        idempotency_key=str(uuid.uuid4()),
    )


@pytest.mark.django_db
class TestRefunds:
    def test_request_list_approve_complete_credits_wallet(self):
        buyer_uid = str(uuid.uuid4())
        order_no = _paid_wallet_order(buyer_uid)  # MC balance: 100 - 10 = 90
        buyer = _client_for_uid(buyer_uid)
        admin = _admin_client()

        req = buyer.post(
            f"/api/v1/commerce/orders/{order_no}/refund-requests",
            {"reason": "broken"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert req.status_code == 201
        body = req.json()
        rid = body["id"]
        assert body["status"] == "requested"
        assert body["requested_amount"] == {"amount": "10.0000", "currency": "MC"}

        listed = buyer.get(f"/api/v1/commerce/orders/{order_no}/refund-requests")
        assert len(listed.json()["results"]) == 1

        approved = admin.post(
            f"/api/v1/commerce/refund-requests/{rid}/approve",
            {"admin_note": "ok"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"

        completed = admin.post(
            f"/api/v1/commerce/refund-requests/{rid}/complete",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "refunded"
        # Wallet credited back: 90 + 10 = 100.
        assert economy.get_balance(user_id=buyer_uid, currency="MC") == Decimal("100.0000")

    def test_duplicate_active_request_conflicts(self):
        buyer_uid = str(uuid.uuid4())
        order_no = _paid_wallet_order(buyer_uid)
        buyer = _client_for_uid(buyer_uid)
        buyer.post(
            f"/api/v1/commerce/orders/{order_no}/refund-requests",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        dup = buyer.post(
            f"/api/v1/commerce/orders/{order_no}/refund-requests",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert dup.status_code == 409
        assert dup.json()["error"]["code"] == "REFUND_ALREADY_ACTIVE"

    def test_pending_order_not_refundable(self):
        order = _pending_order(_store())
        resp = _client_for_uid(str(order.buyer_user_id)).post(
            f"/api/v1/commerce/orders/{order.order_no}/refund-requests",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "ORDER_NOT_REFUNDABLE"

    def test_reject_leaves_wallet_untouched(self):
        buyer_uid = str(uuid.uuid4())
        order_no = _paid_wallet_order(buyer_uid)  # balance 90
        buyer = _client_for_uid(buyer_uid)
        admin = _admin_client()
        rid = buyer.post(
            f"/api/v1/commerce/orders/{order_no}/refund-requests",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        ).json()["id"]
        resp = admin.post(
            f"/api/v1/commerce/refund-requests/{rid}/reject",
            {"admin_note": "no"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        assert economy.get_balance(user_id=buyer_uid, currency="MC") == Decimal("90.0000")

    def test_complete_requires_approved(self):
        buyer_uid = str(uuid.uuid4())
        order_no = _paid_wallet_order(buyer_uid)
        buyer = _client_for_uid(buyer_uid)
        rid = buyer.post(
            f"/api/v1/commerce/orders/{order_no}/refund-requests",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        ).json()["id"]
        resp = _admin_client().post(
            f"/api/v1/commerce/refund-requests/{rid}/complete",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "REFUND_NOT_APPROVED"

    def test_approve_requires_admin(self):
        buyer_uid = str(uuid.uuid4())
        order_no = _paid_wallet_order(buyer_uid)
        buyer = _client_for_uid(buyer_uid)
        rid = buyer.post(
            f"/api/v1/commerce/orders/{order_no}/refund-requests",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        ).json()["id"]
        resp = buyer.post(
            f"/api/v1/commerce/refund-requests/{rid}/approve",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 403


@pytest.mark.django_db
class TestQRResolve:
    def test_resolve_pending_order(self):
        store = _store()
        order = _pending_order(store)
        resp = APIClient().post(
            "/api/v1/commerce/payment-qr/resolve",
            {"qr_payload": {"order_no": order.order_no}},
            format="json",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["order_no"] == order.order_no
        assert body["product_title"] == "Widget"
        assert body["price"] == {"amount": "29.9900", "currency": "USD"}
        assert body["seller_name"] == store.name
        assert body["status"] == "pending_payment"

    def test_resolve_unknown_order(self):
        resp = APIClient().post(
            "/api/v1/commerce/payment-qr/resolve",
            {"qr_payload": {"order_no": "CO-NOPE"}},
            format="json",
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "QR_INVALID_OR_EXPIRED"


@pytest.mark.django_db
class TestPublicStore:
    def test_public_store_and_products(self):
        store = _store(slug="my-shop")
        _product(store, currency="USD", title="Active", status=Product.ACTIVE)
        _product(store, currency="USD", title="Draft", status=Product.DRAFT)

        page = APIClient().get("/api/v1/public/stores/my-shop")
        assert page.status_code == 200
        assert page.json()["name"] == "Store"
        assert "stats" in page.json()

        products = APIClient().get("/api/v1/public/stores/my-shop/products")
        assert products.status_code == 200
        assert {p["title"] for p in products.json()["results"]} == {"Active"}

    def test_inactive_store_404(self):
        _store(slug="hidden", active=False)
        assert APIClient().get("/api/v1/public/stores/hidden").status_code == 404


@pytest.mark.django_db
class TestBlockchainPayment:
    def test_blockchain_order_surfaces_pay_to_address(self, settings):
        settings.LTT_RECEIVE_ADDRESS = "0xLTTRECEIVE"
        store = _store()
        product = _product(store, currency="THB-LTT", price="50")
        buyer = _client_for_uid(str(uuid.uuid4()))

        resp = buyer.post(
            "/api/v1/commerce/orders",
            {
                "product_id": str(product.id),
                "quantity": 1,
                "payment_provider": "blockchain",
                "payment_asset": "THB-LTT",
                "blockchain_network": "ltt",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "pending_payment"
        assert body["payment"]["provider"] == "blockchain"
        assert body["payment"]["pay_to_address"] == "0xLTTRECEIVE"

    def test_blockchain_requires_network(self):
        store = _store()
        product = _product(store, currency="THB-LTT", price="50")
        resp = _client_for_uid(str(uuid.uuid4())).post(
            "/api/v1/commerce/orders",
            {
                "product_id": str(product.id),
                "quantity": 1,
                "payment_provider": "blockchain",
                "payment_asset": "THB-LTT",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "ORDER_NETWORK_REQUIRED"
