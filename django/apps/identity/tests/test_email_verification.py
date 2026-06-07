"""Tests for identity email verification (V2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.identity import services
from apps.identity.models import EmailVerificationToken, User
from libs.errors.exceptions import ValidationError

PATCH_EMIT = "apps.identity.services._emit_outbox"
PATCH_AUDIT = "apps.identity.services._record_audit"


def _user(*, verified: bool = False) -> User:
    return User.objects.create(
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        display_name="U",
        email_verified=verified,
    )


def _client(uid: str) -> APIClient:
    from libs.jwt_auth.authentication import JWTUser

    client = APIClient()
    client.force_authenticate(
        user=JWTUser({"sub": str(uid), "type": "access", "jti": str(uuid.uuid4())})
    )
    return client


@pytest.mark.django_db
class TestEmailVerificationService:
    @patch(PATCH_AUDIT)
    @patch(PATCH_EMIT)
    def test_request_then_confirm(self, mock_emit, mock_audit):
        user = _user()
        services.request_email_verification(user_id=str(user.id))
        assert EmailVerificationToken.objects.filter(user=user).count() == 1
        raw = mock_emit.call_args.kwargs["payload"]["verification_token"]

        result = services.confirm_email_verification(verification_token=raw)
        assert result == {"email_verified": True}
        user.refresh_from_db()
        assert user.email_verified is True
        assert user.email_verified_at is not None

    @patch(PATCH_EMIT)
    def test_request_noop_when_already_verified(self, mock_emit):
        user = _user(verified=True)
        services.request_email_verification(user_id=str(user.id))
        assert EmailVerificationToken.objects.filter(user=user).count() == 0
        mock_emit.assert_not_called()

    def test_request_unknown_user(self):
        from libs.errors.exceptions import NotFoundError

        with pytest.raises(NotFoundError) as exc:
            services.request_email_verification(user_id=str(uuid.uuid4()))
        assert exc.value.code == "USER_NOT_FOUND"

    def test_confirm_invalid_token(self):
        with pytest.raises(ValidationError) as exc:
            services.confirm_email_verification(verification_token="nope")
        assert exc.value.code == "AUTH_VERIFICATION_TOKEN_INVALID"

    @patch(PATCH_AUDIT)
    @patch(PATCH_EMIT)
    def test_confirm_used_token_rejected(self, mock_emit, mock_audit):
        user = _user()
        services.request_email_verification(user_id=str(user.id))
        raw = mock_emit.call_args.kwargs["payload"]["verification_token"]
        services.confirm_email_verification(verification_token=raw)
        with pytest.raises(ValidationError) as exc:
            services.confirm_email_verification(verification_token=raw)
        assert exc.value.code == "AUTH_VERIFICATION_TOKEN_INVALID"

    def test_confirm_expired_token(self):
        user = _user()
        raw = "rawtoken-abc"
        EmailVerificationToken.objects.create(
            user=user,
            token_hash=services._hash_token(raw),
            expires_at=datetime.now(tz=UTC) - timedelta(hours=1),
        )
        with pytest.raises(ValidationError) as exc:
            services.confirm_email_verification(verification_token=raw)
        assert exc.value.code == "AUTH_VERIFICATION_TOKEN_EXPIRED"


@pytest.mark.django_db
class TestEmailVerificationHTTP:
    def test_request_requires_auth(self):
        assert APIClient().post("/api/v1/auth/email/verify/request").status_code == 401

    @patch(PATCH_AUDIT)
    @patch(PATCH_EMIT)
    def test_request_and_confirm_endpoints(self, mock_emit, mock_audit):
        user = _user()
        req = _client(str(user.id)).post(
            "/api/v1/auth/email/verify/request", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())
        )
        assert req.status_code == 204
        raw = mock_emit.call_args.kwargs["payload"]["verification_token"]

        confirm = APIClient().post(
            "/api/v1/auth/email/verify/confirm",
            {"verification_token": raw},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert confirm.status_code == 200
        assert confirm.json() == {"email_verified": True}
        user.refresh_from_db()
        assert user.email_verified is True

    def test_confirm_invalid_token_400(self):
        resp = APIClient().post(
            "/api/v1/auth/email/verify/confirm",
            {"verification_token": "bad"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "AUTH_VERIFICATION_TOKEN_INVALID"
