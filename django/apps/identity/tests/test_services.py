"""Service-layer tests for identity.

Uses pytest-django (pytest.mark.django_db) and unittest.mock to isolate
cross-app calls (economy, events, audit).
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth.hashers import make_password

from apps.identity import services
from apps.identity.models import Follow, User, UserPreferences, UserSession
from libs.errors.exceptions import (
    AuthError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableError,
    ValidationError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PATCH_ISSUE = "apps.identity.services.issue_token_pair"
PATCH_EMIT = "apps.identity.services._emit_outbox"
PATCH_WALLETS = "apps.identity.services._create_wallets"
PATCH_AUDIT = "apps.identity.services._record_audit"

FAKE_TOKENS = {
    "access": "access.token",
    "refresh": "refresh.token",
    "refresh_jti": str(uuid.uuid4()),
    "expires_at": "2026-06-04T10:15:00Z",
}


def _make_user(email: str = "user@example.com", password: str = "Password1", **kwargs) -> User:
    return User.objects.create(
        email=email,
        password_hash=make_password(password),
        display_name="Test User",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRegister:
    def test_success(self):
        with (
            patch(PATCH_ISSUE, return_value=FAKE_TOKENS),
            patch(PATCH_EMIT),
            patch(PATCH_WALLETS),
            patch(PATCH_AUDIT),
        ):
            result = services.register(
                email="New@Example.COM",
                password="Password1",
                display_name="New User",
                idempotency_key="key-1",
            )

        assert result["user"]["email"] == "new@example.com"
        assert "tokens" in result
        assert User.objects.filter(email="new@example.com").exists()
        assert UserPreferences.objects.filter(user__email="new@example.com").exists()

    def test_duplicate_email_raises_conflict(self):
        _make_user(email="dup@example.com")
        with pytest.raises(ConflictError) as exc_info:
            services.register(
                email="dup@example.com",
                password="Password1",
                display_name="D",
                idempotency_key="key-2",
            )
        assert exc_info.value.code == "AUTH_EMAIL_ALREADY_EXISTS"

    def test_weak_password_raises_validation(self):
        with pytest.raises(ValidationError) as exc_info:
            services.register(
                email="weak@example.com",
                password="short",
                display_name="W",
                idempotency_key="key-3",
            )
        assert exc_info.value.code == "VALIDATION_PASSWORD_TOO_WEAK"

    def test_invalid_email_raises_validation(self):
        with pytest.raises(ValidationError) as exc_info:
            services.register(
                email="not-an-email",
                password="Password1",
                display_name="W",
                idempotency_key="key-4",
            )
        assert exc_info.value.code == "VALIDATION_INVALID_EMAIL"

    def test_email_normalized(self):
        with (
            patch(PATCH_ISSUE, return_value=FAKE_TOKENS),
            patch(PATCH_EMIT),
            patch(PATCH_WALLETS),
            patch(PATCH_AUDIT),
        ):
            services.register(
                email="  UPPER@EXAMPLE.COM  ",
                password="Password1",
                display_name="U",
                idempotency_key="key-5",
            )
        assert User.objects.filter(email="upper@example.com").exists()


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLogin:
    def test_success(self):
        _make_user(email="login@example.com", password="Password1")

        with (
            patch(PATCH_ISSUE, return_value=FAKE_TOKENS),
            patch(PATCH_EMIT),
        ):
            result = services.login(email="login@example.com", password="Password1")

        assert "tokens" in result
        assert "session" in result
        assert UserSession.objects.filter(user__email="login@example.com").exists()

    def test_wrong_password_raises_auth_error(self):
        _make_user(email="bad@example.com", password="Password1")
        with pytest.raises(AuthError) as exc_info:
            services.login(email="bad@example.com", password="WrongPass1")
        assert exc_info.value.code == "AUTH_INVALID_CREDENTIALS"

    def test_inactive_user_raises_forbidden(self):
        _make_user(email="inactive@example.com", password="Password1", is_active=False)
        with pytest.raises(ForbiddenError) as exc_info:
            services.login(email="inactive@example.com", password="Password1")
        assert exc_info.value.code == "AUTH_ACCOUNT_DEACTIVATED"

    def test_unknown_email_raises_auth_error(self):
        with pytest.raises(AuthError):
            services.login(email="ghost@example.com", password="Password1")


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRefreshToken:
    def test_success(self):
        user = _make_user()
        jti = uuid.uuid4()
        session = UserSession.objects.create(user=user, refresh_jti=jti)

        with (
            patch("apps.identity.services._decode_refresh_token") as mock_decode,
            patch(PATCH_ISSUE, return_value={**FAKE_TOKENS, "refresh_jti": str(uuid.uuid4())}),
        ):
            mock_decode.return_value = {"jti": str(jti), "sub": str(user.id), "type": "refresh"}
            result = services.refresh_token(refresh_jwt="fake.refresh.token")

        assert "tokens" in result
        session.refresh_from_db()
        assert session.refresh_jti != jti  # jti rotated

    def test_revoked_session_raises_auth_error(self):
        with (
            patch("apps.identity.services._decode_refresh_token") as mock_decode,
        ):
            mock_decode.return_value = {
                "jti": str(uuid.uuid4()),
                "sub": str(uuid.uuid4()),
                "type": "refresh",
            }
            with pytest.raises(AuthError) as exc_info:
                services.refresh_token(refresh_jwt="fake.refresh.token")
        assert exc_info.value.code == "AUTH_SESSION_REVOKED"


# ---------------------------------------------------------------------------
# logout
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLogout:
    def test_logout_removes_session(self):
        user = _make_user()
        jti = uuid.uuid4()
        UserSession.objects.create(user=user, refresh_jti=jti)

        with patch("apps.identity.services._decode_refresh_token") as mock_decode:
            mock_decode.return_value = {"jti": str(jti), "sub": str(user.id), "type": "refresh"}
            services.logout(refresh_jwt="fake", user_id=str(user.id))

        assert not UserSession.objects.filter(user=user).exists()

    def test_logout_invalid_token_is_noop(self):
        user = _make_user(email="logout2@example.com")
        with patch("apps.identity.services._decode_refresh_token", side_effect=AuthError()):
            services.logout(refresh_jwt="bad", user_id=str(user.id))
        # No error raised


# ---------------------------------------------------------------------------
# follow
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFollow:
    def test_follow_success(self):
        a = _make_user("a@example.com")
        b = _make_user("b@example.com")

        with patch(PATCH_EMIT):
            result = services.follow_user(follower_id=str(a.id), target_id=str(b.id))

        assert result["is_following"] is True
        assert Follow.objects.filter(follower=a, target=b).exists()
        b.refresh_from_db()
        assert b.follower_count == 1

    def test_self_follow_raises_unprocessable(self):
        a = _make_user("self@example.com")
        with pytest.raises(UnprocessableError) as exc_info:
            services.follow_user(follower_id=str(a.id), target_id=str(a.id))
        assert exc_info.value.code == "FOLLOW_SELF_FORBIDDEN"

    def test_follow_idempotent(self):
        a = _make_user("fi1@example.com")
        b = _make_user("fi2@example.com")

        with patch(PATCH_EMIT):
            services.follow_user(follower_id=str(a.id), target_id=str(b.id))
            services.follow_user(follower_id=str(a.id), target_id=str(b.id))

        # Only one Follow row
        assert Follow.objects.filter(follower=a, target=b).count() == 1
        b.refresh_from_db()
        assert b.follower_count == 1

    def test_follow_user_not_found(self):
        a = _make_user("fn@example.com")
        with pytest.raises(NotFoundError):
            services.follow_user(follower_id=str(a.id), target_id=str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# change_password
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestChangePassword:
    def test_success(self):
        user = _make_user(email="cp@example.com", password="OldPass1")
        with (
            patch(PATCH_EMIT),
            patch(PATCH_AUDIT),
        ):
            services.change_password(
                user_id=str(user.id),
                current_password="OldPass1",
                new_password="NewPass1",
            )
        user.refresh_from_db()
        from django.contrib.auth.hashers import check_password

        assert check_password("NewPass1", user.password_hash)

    def test_wrong_current_password_raises_auth_error(self):
        user = _make_user(email="cp2@example.com", password="OldPass1")
        with pytest.raises(AuthError) as exc_info:
            services.change_password(
                user_id=str(user.id),
                current_password="WrongPass1",
                new_password="NewPass1",
            )
        assert exc_info.value.code == "AUTH_INVALID_CREDENTIALS"

    def test_weak_new_password_raises_validation(self):
        user = _make_user(email="cp3@example.com", password="OldPass1")
        with pytest.raises(ValidationError) as exc_info:
            services.change_password(
                user_id=str(user.id),
                current_password="OldPass1",
                new_password="weak",
            )
        assert exc_info.value.code == "VALIDATION_PASSWORD_TOO_WEAK"

    def test_revoke_other_sessions(self):
        user = _make_user(email="cpr@example.com", password="OldPass1")
        UserSession.objects.create(user=user, refresh_jti=uuid.uuid4())
        UserSession.objects.create(user=user, refresh_jti=uuid.uuid4())

        with (
            patch(PATCH_EMIT),
            patch(PATCH_AUDIT),
        ):
            services.change_password(
                user_id=str(user.id),
                current_password="OldPass1",
                new_password="NewPass1",
                revoke_other_sessions=True,
            )

        assert UserSession.objects.filter(user=user).count() == 0
