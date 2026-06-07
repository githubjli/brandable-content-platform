"""Tests for content.live products binding (content-live.md §1, §6)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.commerce.models import Product, SellerStore
from apps.content.live.models import LiveStream, LiveStreamProduct
from apps.identity.models import User


def _user(*, creator: bool = True) -> User:
    return User.objects.create(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        display_name="U",
        is_creator=creator,
    )


def _client(uid: str) -> APIClient:
    from libs.jwt_auth.authentication import JWTUser

    client = APIClient()
    client.force_authenticate(
        user=JWTUser({"sub": str(uid), "type": "access", "jti": str(uuid.uuid4())})
    )
    return client


def _product(owner: User, *, title: str = "Widget", status: str = Product.ACTIVE) -> Product:
    store = SellerStore.objects.create(
        owner_user_id=owner.id, slug=f"store-{uuid.uuid4().hex[:8]}", name="Store"
    )
    return Product.objects.create(
        store=store,
        title=title,
        price_amount=Decimal("29.99"),
        price_currency="USD",
        stock=5,
        status=status,
    )


def _stream(owner: User, **over) -> LiveStream:
    return LiveStream.objects.create(owner_user_id=owner.id, title="S", **over)


@pytest.mark.django_db
class TestBindProduct:
    def test_bind_and_list_with_card(self):
        owner = _user()
        s = _stream(owner)
        p = _product(owner, title="Cool Thing")
        c = _client(str(owner.id))
        resp = c.post(
            f"/api/v1/content/live/me/streams/{s.id}/products",
            {"product_id": str(p.id), "is_featured": True, "sort_order": 2},
            format="json",
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["product_id"] == str(p.id)
        assert body["is_featured"] is True
        assert body["product"]["title"] == "Cool Thing"

        listing = c.get(f"/api/v1/content/live/me/streams/{s.id}/products").json()
        assert len(listing["results"]) == 1

    def test_bind_unknown_product_404(self):
        owner = _user()
        s = _stream(owner)
        resp = _client(str(owner.id)).post(
            f"/api/v1/content/live/me/streams/{s.id}/products",
            {"product_id": str(uuid.uuid4())},
            format="json",
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "PRODUCT_NOT_FOUND"

    def test_bind_duplicate_conflict(self):
        owner = _user()
        s = _stream(owner)
        p = _product(owner)
        c = _client(str(owner.id))
        body = {"product_id": str(p.id)}
        assert (
            c.post(
                f"/api/v1/content/live/me/streams/{s.id}/products", body, format="json"
            ).status_code
            == 201
        )
        dup = c.post(f"/api/v1/content/live/me/streams/{s.id}/products", body, format="json")
        assert dup.status_code == 409
        assert dup.json()["error"]["code"] == "LIVE_PRODUCT_ALREADY_BOUND"

    def test_bind_requires_ownership(self):
        owner = _user()
        other = _user()
        s = _stream(owner)
        p = _product(owner)
        resp = _client(str(other.id)).post(
            f"/api/v1/content/live/me/streams/{s.id}/products",
            {"product_id": str(p.id)},
            format="json",
        )
        assert resp.status_code == 404  # stream not found for non-owner


@pytest.mark.django_db
class TestUpdateUnbind:
    def test_patch_binding(self):
        owner = _user()
        s = _stream(owner)
        p = _product(owner)
        b = LiveStreamProduct.objects.create(stream=s, product_id=p.id)
        resp = _client(str(owner.id)).patch(
            f"/api/v1/content/live/me/streams/{s.id}/products/{b.id}",
            {"is_featured": True, "sort_order": 5, "is_active": False},
            format="json",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_featured"] is True
        assert body["sort_order"] == 5
        assert body["is_active"] is False

    def test_delete_binding(self):
        owner = _user()
        s = _stream(owner)
        p = _product(owner)
        b = LiveStreamProduct.objects.create(stream=s, product_id=p.id)
        resp = _client(str(owner.id)).delete(
            f"/api/v1/content/live/me/streams/{s.id}/products/{b.id}"
        )
        assert resp.status_code == 204
        assert not LiveStreamProduct.objects.filter(id=b.id).exists()

    def test_unknown_binding_404(self):
        owner = _user()
        s = _stream(owner)
        resp = _client(str(owner.id)).delete(
            f"/api/v1/content/live/me/streams/{s.id}/products/{uuid.uuid4()}"
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "LIVE_PRODUCT_NOT_FOUND"


@pytest.mark.django_db
class TestViewerProductsList:
    def test_public_list_active_only(self):
        owner = _user()
        s = _stream(owner)
        p1 = _product(owner, title="A")
        p2 = _product(owner, title="B")
        LiveStreamProduct.objects.create(stream=s, product_id=p1.id, is_active=True)
        LiveStreamProduct.objects.create(stream=s, product_id=p2.id, is_active=False)
        resp = APIClient().get(f"/api/v1/content/live/streams/{s.id}/products")
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert {r["product"]["title"] for r in results} == {"A"}

    def test_inactive_product_card_is_none(self):
        owner = _user()
        s = _stream(owner)
        p = _product(owner, status=Product.ARCHIVED)
        LiveStreamProduct.objects.create(stream=s, product_id=p.id, is_active=True)
        resp = APIClient().get(f"/api/v1/content/live/streams/{s.id}/products")
        results = resp.json()["results"]
        assert results[0]["product"] is None

    def test_private_stream_404_for_non_owner(self):
        owner = _user()
        s = _stream(owner, visibility=LiveStream.PRIVATE)
        assert APIClient().get(f"/api/v1/content/live/streams/{s.id}/products").status_code == 404
