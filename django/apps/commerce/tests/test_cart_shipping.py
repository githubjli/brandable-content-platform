"""Tests for Week 18 / V2 commerce: cart, shipping addresses, order list, and
the create_order shipping-address snapshot (commerce.md §2, §3, §10).

Cart + shipping CRUD go through the authenticated HTTP stack; the order/address
snapshot logic is exercised at the service layer (it needs the payment chain).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.commerce import services as commerce
from apps.commerce.models import CartItem, Product, SellerStore, ShippingAddress
from apps.economy import services as economy
from apps.events.dispatcher import dispatch_pending_batch
from apps.identity.models import User
from libs.errors.exceptions import NotFoundError, ValidationError


def _authed_client() -> tuple[APIClient, str]:
    from libs.jwt_auth.authentication import JWTUser

    user_id = str(uuid.uuid4())
    client = APIClient()
    client.force_authenticate(
        user=JWTUser({"sub": user_id, "type": "access", "jti": str(uuid.uuid4())})
    )
    return client, user_id


def _store() -> SellerStore:
    owner = User.objects.create(
        email=f"o-{uuid.uuid4().hex[:8]}@example.com", password_hash="x", display_name="Owner"
    )
    return SellerStore.objects.create(
        owner_user_id=owner.id, slug=f"store-{uuid.uuid4().hex[:8]}", name="Store"
    )


def _product(
    *,
    price: str = "10",
    currency: str = "USD",
    physical: bool = False,
    status: str = Product.ACTIVE,
) -> Product:
    return Product.objects.create(
        store=_store(),
        title="Widget",
        price_amount=Decimal(price),
        price_currency=currency,
        stock=5,
        is_physical=physical,
        status=status,
    )


def _addr_payload(**over) -> dict:
    base = {
        "recipient_name": "Jane Doe",
        "phone": "+66123",
        "street_address": "123 Main St",
        "city": "Bangkok",
        "state": "BKK",
        "postal_code": "10100",
        "country": "TH",
    }
    base.update(over)
    return base


@pytest.mark.django_db
class TestCart:
    def test_requires_auth(self):
        assert APIClient().get("/api/v1/commerce/cart").status_code == 401

    def test_add_then_list_and_count(self):
        client, _ = _authed_client()
        product = _product()

        resp = client.post(
            "/api/v1/commerce/cart",
            {"product_id": str(product.id)},
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 201
        assert resp.json()["product"]["id"] == str(product.id)

        listed = client.get("/api/v1/commerce/cart")
        assert listed.status_code == 200
        assert len(listed.json()["results"]) == 1
        assert client.get("/api/v1/commerce/cart/count").json() == {"count": 1}

    def test_add_is_idempotent_per_product(self):
        client, uid = _authed_client()
        product = _product()
        for _ in range(2):
            client.post(
                "/api/v1/commerce/cart",
                {"product_id": str(product.id)},
                HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
            )
        assert CartItem.objects.filter(user_id=uid).count() == 1

    def test_add_unknown_product_404(self):
        client, _ = _authed_client()
        resp = client.post(
            "/api/v1/commerce/cart",
            {"product_id": str(uuid.uuid4())},
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "PRODUCT_NOT_FOUND"

    def test_delete_item(self):
        client, uid = _authed_client()
        product = _product()
        add = client.post(
            "/api/v1/commerce/cart",
            {"product_id": str(product.id)},
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        item_id = add.json()["id"]

        resp = client.delete(f"/api/v1/commerce/cart/{item_id}")
        assert resp.status_code == 204
        assert CartItem.objects.filter(user_id=uid).count() == 0

    def test_delete_missing_404(self):
        client, _ = _authed_client()
        resp = client.delete(f"/api/v1/commerce/cart/{uuid.uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "CART_ITEM_NOT_FOUND"

    def test_cart_is_per_user(self):
        client_a, _ = _authed_client()
        client_b, _ = _authed_client()
        product = _product()
        client_a.post(
            "/api/v1/commerce/cart",
            {"product_id": str(product.id)},
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert client_b.get("/api/v1/commerce/cart/count").json() == {"count": 0}


@pytest.mark.django_db
class TestShippingAddresses:
    def test_first_address_is_default(self):
        client, _ = _authed_client()
        resp = client.post(
            "/api/v1/commerce/shipping-addresses",
            _addr_payload(),
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["is_default"] is True
        assert body["recipient_name"] == "Jane Doe"

    def test_second_address_not_default_unless_flagged(self):
        client, _ = _authed_client()
        client.post(
            "/api/v1/commerce/shipping-addresses",
            _addr_payload(recipient_name="First"),
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        second = client.post(
            "/api/v1/commerce/shipping-addresses",
            _addr_payload(recipient_name="Second"),
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert second.json()["is_default"] is False

    def test_setting_default_unsets_others(self):
        client, uid = _authed_client()
        a = client.post(
            "/api/v1/commerce/shipping-addresses",
            _addr_payload(recipient_name="A"),
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        ).json()
        b = client.post(
            "/api/v1/commerce/shipping-addresses",
            _addr_payload(recipient_name="B"),
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        ).json()

        client.patch(
            f"/api/v1/commerce/shipping-addresses/{b['id']}",
            {"is_default": True},
            format="json",
        )
        defaults = set(
            ShippingAddress.objects.filter(user_id=uid, is_default=True).values_list(
                "id", flat=True
            )
        )
        assert defaults == {uuid.UUID(b["id"])}
        assert a["id"] not in {str(d) for d in defaults}

    def test_delete_default_promotes_another(self):
        client, uid = _authed_client()
        a = client.post(
            "/api/v1/commerce/shipping-addresses",
            _addr_payload(recipient_name="A"),
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        ).json()
        client.post(
            "/api/v1/commerce/shipping-addresses",
            _addr_payload(recipient_name="B"),
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        # a is the default (created first); delete it.
        assert client.delete(f"/api/v1/commerce/shipping-addresses/{a['id']}").status_code == 204
        remaining = ShippingAddress.objects.filter(user_id=uid)
        assert remaining.count() == 1
        assert remaining.first().is_default is True

    def test_get_and_missing(self):
        client, _ = _authed_client()
        created = client.post(
            "/api/v1/commerce/shipping-addresses",
            _addr_payload(),
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        ).json()
        assert client.get(f"/api/v1/commerce/shipping-addresses/{created['id']}").status_code == 200
        missing = client.get(f"/api/v1/commerce/shipping-addresses/{uuid.uuid4()}")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "SHIPPING_ADDRESS_NOT_FOUND"

    def test_addresses_are_per_user(self):
        client_a, _ = _authed_client()
        client_b, _ = _authed_client()
        client_a.post(
            "/api/v1/commerce/shipping-addresses",
            _addr_payload(),
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert client_b.get("/api/v1/commerce/shipping-addresses").json()["results"] == []


@pytest.mark.django_db
class TestOrderList:
    def test_lists_only_own_orders_filtered_by_status(self):
        client, uid = _authed_client()
        store = _store()
        paid = _make_order(uid, store, status="paid")
        _make_order(uid, store, status="pending_payment")
        _make_order(str(uuid.uuid4()), store, status="paid")  # other user

        resp = client.get("/api/v1/commerce/orders?status=paid")
        assert resp.status_code == 200
        order_nos = {o["order_no"] for o in resp.json()["results"]}
        assert order_nos == {paid.order_no}

    def test_lists_all_statuses_without_filter(self):
        client, uid = _authed_client()
        store = _store()
        _make_order(uid, store, status="paid")
        _make_order(uid, store, status="pending_payment")
        resp = client.get("/api/v1/commerce/orders")
        assert len(resp.json()["results"]) == 2


@pytest.mark.django_db
class TestCreateOrderShippingSnapshot:
    def test_physical_without_address_rejected(self):
        uid = str(uuid.uuid4())
        economy.create_wallets_for_user(user_id=uid)
        product = _product(price="10", currency="MC", physical=True)
        with pytest.raises(ValidationError) as exc:
            commerce.create_order(
                user_id=uid,
                product_id=str(product.id),
                quantity=1,
                payment_provider="wallet",
                payment_asset="MC",
                idempotency_key="phys1",
            )
        assert exc.value.code == "ORDER_SHIPPING_ADDRESS_REQUIRED"
        product.refresh_from_db()
        assert product.stock == 5  # rolled back

    def test_unknown_address_rejected(self):
        uid = str(uuid.uuid4())
        economy.create_wallets_for_user(user_id=uid)
        product = _product(price="10", currency="MC", physical=True)
        with pytest.raises(NotFoundError) as exc:
            commerce.create_order(
                user_id=uid,
                product_id=str(product.id),
                quantity=1,
                payment_provider="wallet",
                payment_asset="MC",
                idempotency_key="phys2",
                shipping_address_id=str(uuid.uuid4()),
            )
        assert exc.value.code == "SHIPPING_ADDRESS_NOT_FOUND"

    def test_address_is_snapshotted_onto_order(self):
        uid = str(uuid.uuid4())
        economy.create_wallets_for_user(user_id=uid)
        economy.credit(
            user_id=uid, currency="MC", entry_type="RECHARGE", amount="100", idempotency_key="seed"
        )
        product = _product(price="10", currency="MC", physical=True)
        addr = ShippingAddress.objects.create(
            user_id=uid,
            recipient_name="Jane Doe",
            street_address="123 Main St",
            city="Bangkok",
            country="TH",
            is_default=True,
        )

        order = commerce.create_order(
            user_id=uid,
            product_id=str(product.id),
            quantity=1,
            payment_provider="wallet",
            payment_asset="MC",
            idempotency_key="phys3",
            shipping_address_id=str(addr.id),
        )
        snap = order["shipping_address_snapshot"]
        assert snap["recipient_name"] == "Jane Doe"
        assert snap["country"] == "TH"
        assert snap["id"] == str(addr.id)
        dispatch_pending_batch()


def _make_order(user_id, store, *, status: str):
    """Create a ProductOrder row directly (bypasses the payment chain)."""
    from apps.commerce.models import ProductOrder

    product = Product.objects.create(
        store=store, title="P", price_amount=Decimal("10"), price_currency="USD", stock=1
    )
    return ProductOrder.objects.create(
        order_no=f"CO-{uuid.uuid4().hex[:12].upper()}",
        buyer_user_id=user_id,
        product=product,
        store=store,
        quantity=1,
        currency="USD",
        subtotal=Decimal("10"),
        platform_fee=Decimal("0.5"),
        seller_receivable=Decimal("9.5"),
        payment_provider="wallet",
        payment_asset="USD",
        status=status,
        idempotency_key=str(uuid.uuid4()),
    )
