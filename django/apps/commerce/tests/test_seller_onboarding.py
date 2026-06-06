"""Tests for Week 19 / V2 commerce: seller onboarding (commerce.md §5, §6).

Application submit/duplicate, admin approve/reject (sets identity.is_seller),
and store create gating + management — through the authenticated HTTP stack.
"""

from __future__ import annotations

import uuid

import pytest
from rest_framework.test import APIClient

from apps.commerce.models import SellerApplication, SellerStore
from apps.identity.models import User


def _user() -> User:
    return User.objects.create(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com", password_hash="x", display_name="U"
    )


def _client_for(user: User) -> APIClient:
    from libs.jwt_auth.authentication import JWTUser

    client = APIClient()
    client.force_authenticate(
        user=JWTUser({"sub": str(user.id), "type": "access", "jti": str(uuid.uuid4())})
    )
    return client


def _admin_client() -> APIClient:
    from libs.jwt_auth.authentication import JWTUser

    client = APIClient()
    client.force_authenticate(
        user=JWTUser(
            {
                "sub": str(uuid.uuid4()),
                "type": "access",
                "jti": str(uuid.uuid4()),
                "scope": ["admin"],
            }
        )
    )
    return client


def _apply(client: APIClient, **over) -> dict:
    payload = {"business_name": "Acme Co", "tax_id": "TX1", "reason": "selling stuff"}
    payload.update(over)
    return client.post(
        "/api/v1/commerce/seller-applications",
        payload,
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )


@pytest.mark.django_db
class TestSellerApplication:
    def test_submit_creates_pending(self):
        user = _user()
        resp = _apply(_client_for(user))
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "pending"
        assert body["business_name"] == "Acme Co"
        assert body["reviewed_at"] is None

    def test_duplicate_active_application_conflicts(self):
        client = _client_for(_user())
        _apply(client)
        dup = _apply(client)
        assert dup.status_code == 409
        assert dup.json()["error"]["code"] == "SELLER_APPLICATION_ALREADY_EXISTS"

    def test_me_returns_latest_or_404(self):
        user = _user()
        client = _client_for(user)
        assert client.get("/api/v1/commerce/seller-applications/me").status_code == 404
        _apply(client)
        me = client.get("/api/v1/commerce/seller-applications/me")
        assert me.status_code == 200
        assert me.json()["status"] == "pending"

    def test_requires_auth(self):
        assert APIClient().post("/api/v1/commerce/seller-applications", {}).status_code == 401


@pytest.mark.django_db
class TestApplicationReview:
    def test_approve_sets_is_seller_and_allows_reapply_block(self):
        user = _user()
        app = _apply(_client_for(user)).json()

        resp = _admin_client().post(
            f"/api/v1/commerce/seller-applications/{app['id']}/approve",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"
        user.refresh_from_db()
        assert user.is_seller is True

    def test_reject_records_reason(self):
        user = _user()
        app = _apply(_client_for(user)).json()

        resp = _admin_client().post(
            f"/api/v1/commerce/seller-applications/{app['id']}/reject",
            {"reason": "incomplete docs"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "rejected"
        assert body["rejection_reason"] == "incomplete docs"
        user.refresh_from_db()
        assert user.is_seller is False

    def test_approve_requires_admin(self):
        user = _user()
        app = _apply(_client_for(user)).json()
        # Non-admin authenticated user cannot approve.
        resp = _client_for(_user()).post(
            f"/api/v1/commerce/seller-applications/{app['id']}/approve",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 403

    def test_approve_non_pending_conflicts(self):
        user = _user()
        app = _apply(_client_for(user)).json()
        admin = _admin_client()
        admin.post(
            f"/api/v1/commerce/seller-applications/{app['id']}/approve",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        again = admin.post(
            f"/api/v1/commerce/seller-applications/{app['id']}/approve",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "SELLER_APPLICATION_NOT_PENDING"


def _approve_seller(user: User) -> None:
    app = SellerApplication.objects.create(
        user_id=user.id, status=SellerApplication.APPROVED, business_name="Acme"
    )
    assert app.status == "approved"


@pytest.mark.django_db
class TestStoreManagement:
    def test_create_requires_approved_application(self):
        client = _client_for(_user())
        resp = client.post(
            "/api/v1/commerce/store/me",
            {"slug": "my-store", "name": "My Store"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "SELLER_NOT_APPROVED"

    def test_create_and_get_store_with_stats(self):
        user = _user()
        _approve_seller(user)
        client = _client_for(user)

        created = client.post(
            "/api/v1/commerce/store/me",
            {"slug": "my-store", "name": "My Store", "description": "hi"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert created.status_code == 201
        body = created.json()
        assert body["slug"] == "my-store"
        assert body["stats"] == {
            "total_products": 0,
            "total_orders": 0,
            "total_revenue": {"amount": "0.0000", "currency": "USD"},
        }

        fetched = client.get("/api/v1/commerce/store/me")
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "My Store"

    def test_second_store_conflicts(self):
        user = _user()
        _approve_seller(user)
        client = _client_for(user)
        client.post(
            "/api/v1/commerce/store/me",
            {"slug": "store-a", "name": "A"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        dup = client.post(
            "/api/v1/commerce/store/me",
            {"slug": "store-b", "name": "B"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert dup.status_code == 409
        assert dup.json()["error"]["code"] == "STORE_ALREADY_EXISTS"

    def test_slug_taken_conflicts(self):
        taken_owner = _user()
        SellerStore.objects.create(owner_user_id=taken_owner.id, slug="taken", name="T")

        user = _user()
        _approve_seller(user)
        resp = _client_for(user).post(
            "/api/v1/commerce/store/me",
            {"slug": "taken", "name": "Mine"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "STORE_SLUG_TAKEN"

    def test_get_store_404_when_none(self):
        assert _client_for(_user()).get("/api/v1/commerce/store/me").status_code == 404

    def test_patch_updates_name_not_slug(self):
        user = _user()
        _approve_seller(user)
        client = _client_for(user)
        client.post(
            "/api/v1/commerce/store/me",
            {"slug": "keep", "name": "Old"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        resp = client.patch(
            "/api/v1/commerce/store/me",
            {"name": "New", "slug": "ignored"},
            format="json",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "New"
        assert body["slug"] == "keep"
