"""HTTP-level integration tests for identity views.

Tests the full request → view → service → response path.
JWT signing is mocked at the service layer so no real RSA keys are needed.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth.hashers import make_password
from rest_framework.test import APIClient

from apps.identity.models import User, UserPreferences, UserSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_TOKENS = {
    "access": "access.token",
    "refresh": "refresh.token",
    "refresh_jti": str(uuid.uuid4()),
    "expires_at": "2026-06-04T10:15:00Z",
}

PATCH_ISSUE = "apps.identity.services.issue_token_pair"
PATCH_EMIT = "apps.identity.services._emit_outbox"
PATCH_WALLETS = "apps.identity.services._create_wallets"
PATCH_AUDIT = "apps.identity.services._record_audit"


def _make_user(email: str = "user@example.com", password: str = "Password1", **kwargs) -> User:
    return User.objects.create(
        email=email,
        password_hash=make_password(password),
        display_name="Test User",
        **kwargs,
    )


def _authed_client(user: User) -> APIClient:
    """Return an APIClient with a mocked authenticated user.

    We inject a synthetic JWTUser directly onto the client via
    force_authenticate rather than generating a real JWT.
    """
    from libs.jwt_auth.authentication import JWTUser

    client = APIClient()
    jwt_user = JWTUser({"sub": str(user.id), "type": "access", "jti": str(uuid.uuid4())})
    client.force_authenticate(user=jwt_user)
    return client


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRegisterView:
    def test_success_201(self):
        client = APIClient()
        with (
            patch(PATCH_ISSUE, return_value=FAKE_TOKENS),
            patch(PATCH_EMIT),
            patch(PATCH_WALLETS),
            patch(PATCH_AUDIT),
        ):
            response = client.post(
                "/api/v1/auth/register",
                {
                    "email": "reg@example.com",
                    "password": "Password1",
                    "display_name": "Reg User",
                },
                format="json",
                headers={"Idempotency-Key": "reg-key-1"},
                HTTP_IDEMPOTENCY_KEY="reg-key-1",
            )

        assert response.status_code == 201
        data = response.json()
        assert "user" in data
        assert "tokens" in data
        assert data["user"]["email"] == "reg@example.com"

    def test_duplicate_email_409(self):
        _make_user(email="dup@example.com")
        client = APIClient()
        response = client.post(
            "/api/v1/auth/register",
            {"email": "dup@example.com", "password": "Password1", "display_name": "D"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="dup-key-1",
        )
        assert response.status_code == 409

    def test_weak_password_400(self):
        client = APIClient()
        response = client.post(
            "/api/v1/auth/register",
            {"email": "weak@example.com", "password": "abc", "display_name": "W"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="weak-key-1",
        )
        # DRF serializer min_length catches before service layer
        assert response.status_code in (400, 422)

    def test_missing_idempotency_key_400(self):
        client = APIClient()
        response = client.post(
            "/api/v1/auth/register",
            {"email": "nokey@example.com", "password": "Password1", "display_name": "NK"},
            format="json",
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLoginView:
    def test_success_200(self):
        _make_user(email="loginv@example.com", password="Password1")
        client = APIClient()
        with (
            patch(PATCH_ISSUE, return_value=FAKE_TOKENS),
            patch(PATCH_EMIT),
        ):
            response = client.post(
                "/api/v1/auth/login",
                {"email": "loginv@example.com", "password": "Password1"},
                format="json",
            )
        assert response.status_code == 200
        data = response.json()
        assert "tokens" in data
        assert "session" in data

    def test_wrong_password_401(self):
        _make_user(email="wrong@example.com", password="Password1")
        client = APIClient()
        response = client.post(
            "/api/v1/auth/login",
            {"email": "wrong@example.com", "password": "BadPass1"},
            format="json",
        )
        assert response.status_code == 401

    def test_deactivated_403(self):
        _make_user(email="deact@example.com", password="Password1", is_active=False)
        client = APIClient()
        response = client.post(
            "/api/v1/auth/login",
            {"email": "deact@example.com", "password": "Password1"},
            format="json",
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMeView:
    def test_authenticated_200(self):
        user = _make_user(email="me@example.com")
        UserPreferences.objects.create(user=user)
        client = _authed_client(user)
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 200
        data = response.json()
        assert data["user"]["email"] == "me@example.com"
        assert "kyc_status" in data["user"]

    def test_unauthenticated_401(self):
        client = APIClient()
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProfileView:
    def test_get_profile(self):
        user = _make_user(email="profile@example.com")
        client = _authed_client(user)
        response = client.get("/api/v1/account/profile")
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "profile@example.com"
        assert "stats" in data

    def test_patch_profile(self):
        user = _make_user(email="patch@example.com")
        client = _authed_client(user)
        with patch(PATCH_EMIT):
            response = client.patch(
                "/api/v1/account/profile",
                {"display_name": "Updated Name"},
                format="json",
                HTTP_IDEMPOTENCY_KEY="patch-key-1",
            )
        assert response.status_code == 200
        assert response.json()["display_name"] == "Updated Name"


# ---------------------------------------------------------------------------
# Follow
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFollowView:
    def test_follow_200(self):
        follower = _make_user("f1@example.com")
        target = _make_user("t1@example.com")
        client = _authed_client(follower)
        with patch(PATCH_EMIT):
            response = client.post(
                f"/api/v1/public/users/{target.id}/follow",
                format="json",
                HTTP_IDEMPOTENCY_KEY="follow-1",
            )
        assert response.status_code == 200
        assert response.json()["is_following"] is True

    def test_unfollow_204(self):
        from apps.identity.models import Follow

        follower = _make_user("f2@example.com")
        target = _make_user("t2@example.com")
        Follow.objects.create(follower=follower, target=target)
        client = _authed_client(follower)
        with patch(PATCH_EMIT):
            response = client.delete(f"/api/v1/public/users/{target.id}/follow")
        assert response.status_code == 204


# ---------------------------------------------------------------------------
# Public user
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPublicUserView:
    def test_public_user_no_auth(self):
        user = _make_user("pub@example.com")
        client = APIClient()
        response = client.get(f"/api/v1/public/users/{user.id}")
        assert response.status_code == 200
        data = response.json()
        assert "email" not in data
        assert data["display_name"] == "Test User"
        assert data["viewer_context"] is None

    def test_public_user_with_auth(self):
        user = _make_user("pubu@example.com")
        viewer = _make_user("viewer@example.com")
        client = _authed_client(viewer)
        response = client.get(f"/api/v1/public/users/{user.id}")
        assert response.status_code == 200
        assert response.json()["viewer_context"] is not None

    def test_not_found_404(self):
        client = APIClient()
        response = client.get(f"/api/v1/public/users/{uuid.uuid4()}")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSessionsView:
    def test_list_sessions(self):
        user = _make_user("sess@example.com")
        UserSession.objects.create(user=user, refresh_jti=uuid.uuid4(), device_label="iPhone")
        client = _authed_client(user)
        response = client.get("/api/v1/auth/sessions")
        assert response.status_code == 200
        assert len(response.json()["results"]) >= 1

    def test_revoke_session(self):
        user = _make_user("revoke@example.com")
        session = UserSession.objects.create(user=user, refresh_jti=uuid.uuid4())
        client = _authed_client(user)
        response = client.delete(f"/api/v1/auth/sessions/{session.id}")
        assert response.status_code == 204
        assert not UserSession.objects.filter(id=session.id).exists()
