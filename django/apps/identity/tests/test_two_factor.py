"""Tests for identity two-factor auth (TOTP) — V2."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth.hashers import make_password
from rest_framework.test import APIClient

from apps.identity import services, totp
from apps.identity.models import User
from libs.errors.exceptions import AuthError, ConflictError, ValidationError

PATCH_EMIT = "apps.identity.services._emit_outbox"
PATCH_AUDIT = "apps.identity.services._record_audit"
PATCH_ISSUE = "apps.identity.services.issue_token_pair"

PASSWORD = "Sup3rSecret1"


def _user(**over) -> User:
    kwargs = {
        "email": f"{uuid.uuid4().hex[:8]}@example.com",
        "password_hash": make_password(PASSWORD),
        "display_name": "U",
    }
    kwargs.update(over)
    return User.objects.create(**kwargs)


def _client(uid: str) -> APIClient:
    from libs.jwt_auth.authentication import JWTUser

    client = APIClient()
    client.force_authenticate(
        user=JWTUser({"sub": str(uid), "type": "access", "jti": str(uuid.uuid4())})
    )
    return client


def _code(secret: str) -> str:
    import time

    return totp._code_at(secret, int(time.time()) // 30)


class TestTotpHelper:
    def test_verify_accepts_current_code_and_rejects_wrong(self):
        secret = totp.generate_secret()
        assert totp.verify(secret, _code(secret)) is True
        assert totp.verify(secret, "000000") is False
        assert totp.verify(secret, "") is False
        assert totp.verify("", "123456") is False

    def test_provisioning_uri(self):
        uri = totp.provisioning_uri("ABC", account_name="a@b.com")
        assert uri.startswith("otpauth://totp/")
        assert "secret=ABC" in uri


@pytest.mark.django_db
class TestTwoFactorFlow:
    @patch(PATCH_AUDIT)
    @patch(PATCH_EMIT)
    def test_setup_enable_disable(self, mock_emit, mock_audit):
        user = _user()
        setup = services.setup_two_factor(user_id=str(user.id))
        assert setup["otpauth_uri"].startswith("otpauth://")
        secret = setup["secret"]
        user.refresh_from_db()
        assert user.totp_secret == secret
        assert user.two_factor_enabled is False

        services.enable_two_factor(user_id=str(user.id), code=_code(secret))
        user.refresh_from_db()
        assert user.two_factor_enabled is True

        services.disable_two_factor(user_id=str(user.id), code=_code(secret))
        user.refresh_from_db()
        assert user.two_factor_enabled is False
        assert user.totp_secret == ""

    def test_enable_with_bad_code_rejected(self):
        user = _user()
        services.setup_two_factor(user_id=str(user.id))
        with pytest.raises(ValidationError) as exc:
            services.enable_two_factor(user_id=str(user.id), code="000000")
        assert exc.value.code == "AUTH_2FA_INVALID"

    def test_enable_before_setup_rejected(self):
        user = _user()
        with pytest.raises(ValidationError) as exc:
            services.enable_two_factor(user_id=str(user.id), code="123456")
        assert exc.value.code == "AUTH_2FA_NOT_SET_UP"

    @patch(PATCH_AUDIT)
    @patch(PATCH_EMIT)
    def test_setup_when_already_enabled_conflicts(self, mock_emit, mock_audit):
        user = _user()
        secret = services.setup_two_factor(user_id=str(user.id))["secret"]
        services.enable_two_factor(user_id=str(user.id), code=_code(secret))
        with pytest.raises(ConflictError) as exc:
            services.setup_two_factor(user_id=str(user.id))
        assert exc.value.code == "AUTH_2FA_ALREADY_ENABLED"


@pytest.mark.django_db
class TestLoginChallenge:
    FAKE_TOKENS = {
        "access": "a",
        "refresh": "r",
        "refresh_jti": str(uuid.uuid4()),
        "expires_at": "2026-06-07T00:00:00Z",
    }

    def _enable_2fa(self, user: User) -> str:
        with patch(PATCH_EMIT), patch(PATCH_AUDIT):
            secret = services.setup_two_factor(user_id=str(user.id))["secret"]
            services.enable_two_factor(user_id=str(user.id), code=_code(secret))
        return secret

    def test_login_requires_code_when_2fa_enabled(self):
        user = _user()
        self._enable_2fa(user)
        with pytest.raises(AuthError) as exc:
            services.login(email=user.email, password=PASSWORD)
        assert exc.value.code == "AUTH_2FA_REQUIRED"

    def test_login_rejects_bad_code(self):
        user = _user()
        self._enable_2fa(user)
        with pytest.raises(AuthError) as exc:
            services.login(email=user.email, password=PASSWORD, totp_code="000000")
        assert exc.value.code == "AUTH_2FA_INVALID"

    @patch(PATCH_AUDIT)
    @patch(PATCH_EMIT)
    @patch(PATCH_ISSUE)
    def test_login_succeeds_with_valid_code(self, mock_issue, mock_emit, mock_audit):
        mock_issue.return_value = self.FAKE_TOKENS
        user = _user()
        secret = self._enable_2fa(user)
        result = services.login(email=user.email, password=PASSWORD, totp_code=_code(secret))
        assert result["user"]["id"] == str(user.id)

    @patch(PATCH_AUDIT)
    @patch(PATCH_EMIT)
    @patch(PATCH_ISSUE)
    def test_login_without_2fa_unaffected(self, mock_issue, mock_emit, mock_audit):
        mock_issue.return_value = self.FAKE_TOKENS
        user = _user()
        result = services.login(email=user.email, password=PASSWORD)
        assert result["user"]["id"] == str(user.id)


@pytest.mark.django_db
class TestTwoFactorHTTP:
    @patch(PATCH_AUDIT)
    @patch(PATCH_EMIT)
    def test_setup_then_enable_endpoints(self, mock_emit, mock_audit):
        user = _user()
        client = _client(str(user.id))
        setup = client.post("/api/v1/auth/2fa/setup")
        assert setup.status_code == 200
        secret = setup.json()["secret"]
        enable = client.post(
            "/api/v1/auth/2fa/enable",
            {"code": _code(secret)},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        assert enable.status_code == 200
        assert enable.json() == {"two_factor_enabled": True}

    def test_setup_requires_auth(self):
        assert APIClient().post("/api/v1/auth/2fa/setup").status_code == 401
