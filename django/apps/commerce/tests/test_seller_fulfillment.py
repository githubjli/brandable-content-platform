"""Tests for Week 19b / V2 commerce: seller product CRUD and the fulfillment
flow — ship → confirm-received → tracking (commerce.md §7, §8, §3, §11).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.commerce import services as commerce
from apps.commerce.models import Product, ProductOrder, SellerStore, ShippingAddress
from apps.economy import services as economy
from apps.events.dispatcher import dispatch_pending_batch
from apps.identity.models import User


def _user() -> User:
    return User.objects.create(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com", password_hash="x", display_name="U"
    )


def _client_for_uid(uid: str) -> APIClient:
    from libs.jwt_auth.authentication import JWTUser

    client = APIClient()
    client.force_authenticate(
        user=JWTUser({"sub": str(uid), "type": "access", "jti": str(uuid.uuid4())})
    )
    return client


def _store_for(user: User) -> SellerStore:
    return SellerStore.objects.create(
        owner_user_id=user.id, slug=f"store-{uuid.uuid4().hex[:8]}", name="Store"
    )


@pytest.mark.django_db
class TestSellerProductCRUD:
    def test_create_list_update_archive(self):
        seller = _user()
        _store_for(seller)
        client = _client_for_uid(str(seller.id))

        created = client.post(
            "/api/v1/commerce/store/me/products",
            {
                "title": "Mug",
                "price_amount": "12.50",
                "price_currency": "USD",
                "stock_quantity": 7,
                "status": "active",
                "alternate_prices": {"MP": "1200.0000"},
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert created.status_code == 201
        body = created.json()
        pid = body["id"]
        assert body["status"] == "active"
        assert body["stock_quantity"] == 7
        assert body["price"] == {"amount": "12.5000", "currency": "USD"}
        assert body["alternate_prices"] == {"MP": "1200.0000"}

        listed = client.get("/api/v1/commerce/store/me/products")
        assert listed.status_code == 200
        assert len(listed.json()["results"]) == 1

        patched = client.patch(
            f"/api/v1/commerce/store/me/products/{pid}",
            {"title": "Big Mug", "stock_quantity": 3},
            format="json",
        )
        assert patched.status_code == 200
        assert patched.json()["title"] == "Big Mug"
        assert patched.json()["stock_quantity"] == 3

        assert client.delete(f"/api/v1/commerce/store/me/products/{pid}").status_code == 204
        Product.objects.get(id=pid).refresh_from_db()
        assert Product.objects.get(id=pid).status == "archived"

    def test_create_requires_store(self):
        client = _client_for_uid(str(_user().id))  # no store
        resp = client.post(
            "/api/v1/commerce/store/me/products",
            {"title": "X", "price_amount": "1", "price_currency": "USD"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "STORE_NOT_FOUND"

    def test_draft_hidden_from_public_catalog(self):
        seller = _user()
        _store_for(seller)
        client = _client_for_uid(str(seller.id))
        client.post(
            "/api/v1/commerce/store/me/products",
            {"title": "Secret", "price_amount": "1", "price_currency": "USD", "status": "draft"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        # Public catalog only lists active products.
        public = APIClient().get("/api/v1/commerce/shop/products")
        assert public.json()["results"] == []

    def test_products_are_scoped_to_owner_store(self):
        seller_a = _user()
        _store_for(seller_a)
        _client_for_uid(str(seller_a.id)).post(
            "/api/v1/commerce/store/me/products",
            {"title": "A", "price_amount": "1", "price_currency": "USD"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        seller_b = _user()
        _store_for(seller_b)
        listed = _client_for_uid(str(seller_b.id)).get("/api/v1/commerce/store/me/products")
        assert listed.json()["results"] == []


def _make_paid_order(seller: User, buyer_uid: str) -> tuple[SellerStore, str]:
    store = _store_for(seller)
    product = Product.objects.create(
        store=store,
        title="P",
        price_amount=Decimal("10"),
        price_currency="MC",
        stock=5,
        status=Product.ACTIVE,
        is_physical=True,
    )
    economy.create_wallets_for_user(user_id=buyer_uid)
    economy.credit(
        user_id=buyer_uid,
        currency="MC",
        entry_type="RECHARGE",
        amount="100",
        idempotency_key=f"seed-{buyer_uid}",
    )
    addr = ShippingAddress.objects.create(
        user_id=buyer_uid,
        recipient_name="Jane",
        street_address="1 St",
        city="BKK",
        country="TH",
        is_default=True,
    )
    order = commerce.create_order(
        user_id=buyer_uid,
        product_id=str(product.id),
        quantity=1,
        payment_provider="wallet",
        payment_asset="MC",
        idempotency_key=f"ord-{buyer_uid}",
        shipping_address_id=str(addr.id),
    )
    dispatch_pending_batch()  # payments.OrderPaid → ProductOrder paid
    assert ProductOrder.objects.get(order_no=order["order_no"]).status == "paid"
    return store, order["order_no"]


@pytest.mark.django_db
class TestFulfillment:
    def test_ship_confirm_tracking_happy_path(self):
        seller = _user()
        buyer_uid = str(uuid.uuid4())
        _store, order_no = _make_paid_order(seller, buyer_uid)
        seller_client = _client_for_uid(str(seller.id))
        buyer_client = _client_for_uid(buyer_uid)

        shipped = seller_client.post(
            f"/api/v1/commerce/store/me/orders/{order_no}/ship",
            {
                "carrier": "FedEx",
                "tracking_number": "TN1",
                "tracking_url": "https://track.example.com/TN1",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert shipped.status_code == 200
        assert shipped.json()["status"] == "shipping"

        # Buyer can see in-transit tracking.
        track = buyer_client.get(f"/api/v1/commerce/orders/{order_no}/tracking")
        assert track.status_code == 200
        assert track.json()["carrier"] == "FedEx"
        assert track.json()["shipment_status"] == "in_transit"

        confirmed = buyer_client.post(
            f"/api/v1/commerce/orders/{order_no}/confirm-received",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "completed"

        # Shipment flips to delivered on confirm.
        track2 = buyer_client.get(f"/api/v1/commerce/orders/{order_no}/tracking")
        assert track2.json()["shipment_status"] == "delivered"

    def test_seller_order_list_shows_paid(self):
        seller = _user()
        _store, order_no = _make_paid_order(seller, str(uuid.uuid4()))
        resp = _client_for_uid(str(seller.id)).get("/api/v1/commerce/store/me/orders?status=paid")
        assert {o["order_no"] for o in resp.json()["results"]} == {order_no}

    def test_ship_requires_paid_status(self):
        seller = _user()
        store = _store_for(seller)
        product = Product.objects.create(
            store=store, title="P", price_amount=Decimal("1"), price_currency="USD", stock=1
        )
        order = ProductOrder.objects.create(
            order_no=f"CO-{uuid.uuid4().hex[:10].upper()}",
            buyer_user_id=uuid.uuid4(),
            product=product,
            store=store,
            quantity=1,
            currency="USD",
            subtotal=Decimal("1"),
            platform_fee=Decimal("0"),
            seller_receivable=Decimal("1"),
            payment_provider="wallet",
            payment_asset="USD",
            status=ProductOrder.PENDING_PAYMENT,
            idempotency_key=str(uuid.uuid4()),
        )
        resp = _client_for_uid(str(seller.id)).post(
            f"/api/v1/commerce/store/me/orders/{order.order_no}/ship",
            {"carrier": "DHL"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "ORDER_NOT_SHIPPABLE"

    def test_confirm_requires_shipping_status(self):
        seller = _user()
        buyer_uid = str(uuid.uuid4())
        _store, order_no = _make_paid_order(seller, buyer_uid)
        # paid (not yet shipped) → cannot confirm
        resp = _client_for_uid(buyer_uid).post(
            f"/api/v1/commerce/orders/{order_no}/confirm-received",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "ORDER_NOT_CONFIRMABLE"

    def test_other_seller_cannot_ship(self):
        seller = _user()
        _store, order_no = _make_paid_order(seller, str(uuid.uuid4()))
        intruder = _user()
        _store_for(intruder)
        resp = _client_for_uid(str(intruder.id)).post(
            f"/api/v1/commerce/store/me/orders/{order_no}/ship",
            {"carrier": "DHL"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "ORDER_NOT_FOUND"

    def test_tracking_404_before_ship(self):
        seller = _user()
        buyer_uid = str(uuid.uuid4())
        _store, order_no = _make_paid_order(seller, buyer_uid)
        resp = _client_for_uid(buyer_uid).get(f"/api/v1/commerce/orders/{order_no}/tracking")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "SHIPMENT_NOT_FOUND"
