"""Service layer for identity.

All business logic, transaction boundaries, and cross-app calls live here.
Views and serializers must not contain business logic.

Cross-app stubs pattern: import inside try/except so the services function
even when economy / events / audit apps haven't been built yet.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction

from libs.errors.exceptions import (
    AuthError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableError,
    ValidationError,
)
from libs.jwt_auth.signer import issue_token_pair

from .models import (
    CreatorProfile,
    EmailVerificationToken,
    Follow,
    KycDocument,
    KycProfile,
    PasswordResetToken,
    User,
    UserPreferences,
    UserSession,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cross-app stubs
# ---------------------------------------------------------------------------


def _emit_outbox(
    event_type: str, payload: dict, idempotency_key: str, actor_id: str | None = None
) -> None:
    """Emit an OutboxEvent via events.services (EventBus).

    Emit failures — including idempotency-key collisions (EventAlreadyEmitted) — are
    swallowed so a duplicate/transient event never breaks the business write.
    """
    try:
        from apps.events.services import emit

        emit(
            event_type=event_type,
            idempotency_key=idempotency_key,
            payload=payload,
            actor_id=actor_id,
        )
    except Exception:
        logger.debug("_emit_outbox: emit failed; skipping %s", event_type)


def _create_wallets(user_id: str) -> None:
    """Create wallets for user via economy.services.  No-op if app not yet built."""
    try:
        from apps.economy.services import create_wallets_for_user

        create_wallets_for_user(user_id=user_id)
    except Exception:
        logger.debug("_create_wallets: economy app unavailable; skipping for %s", user_id)


def _record_audit(
    action: str,
    *,
    actor_id: str | None,
    target_id: str,
    actor_type: str = "user",
    target_type: str = "User",
    after_state: dict | None = None,
    severity: str = "info",
) -> None:
    """Write an AuditLog row in the caller's transaction (audit.md §4).

    Does NOT swallow: if the audit write fails the business write must roll back.
    """
    from apps.audit.services import record_audit

    record_audit(
        action=action,
        actor_type=actor_type,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
        after_state=after_state,
        severity=severity,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_PASSWORD_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).{8,}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _validate_email(email: str) -> None:
    if not _EMAIL_RE.match(email):
        raise ValidationError(code="VALIDATION_INVALID_EMAIL", message="Invalid email address.")


def _validate_password(password: str) -> None:
    if not _PASSWORD_RE.match(password):
        raise ValidationError(
            code="VALIDATION_PASSWORD_TOO_WEAK",
            message="Password must be at least 8 characters and include at least one letter and one digit.",
        )


def _decode_refresh_token(refresh_jwt: str) -> dict:
    """Decode and validate a refresh JWT (signature + claims)."""
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        with open(settings.JWT_PUBLIC_KEY_PATH, "rb") as f:
            public_key = load_pem_public_key(f.read())

        payload = jwt.decode(
            refresh_jwt,
            public_key,  # type: ignore[arg-type]
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.JWT_ISSUER,
            options={"verify_aud": False},
        )
    except jwt.ExpiredSignatureError as err:
        raise AuthError(code="AUTH_REFRESH_EXPIRED", message="Refresh token has expired.") from err
    except jwt.InvalidTokenError as err:
        raise AuthError(code="AUTH_REFRESH_INVALID", message="Refresh token is invalid.") from err
    except FileNotFoundError as err:
        raise AuthError(
            code="AUTH_REFRESH_INVALID", message="JWT public key not configured."
        ) from err

    if payload.get("type") != "refresh":
        raise AuthError(code="AUTH_REFRESH_INVALID", message="Token type is not refresh.")

    return payload


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _rehash_setter(user: User):
    """Build a setter that upgrades a user's stored hash to the preferred algorithm.

    Passed to check_password: Django invokes it only on a *successful* match whose
    stored hash uses a non-preferred (legacy/migrated) algorithm. This gives the
    "first successful login auto-rehashes" behaviour the migration plan promises.
    """

    def setter(raw_password: str) -> None:
        user.password_hash = make_password(raw_password)
        user.save(update_fields=["password_hash", "updated_at"])

    return setter


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _serialize_user(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
        "is_creator": user.is_creator,
        "is_admin": user.is_admin,
        "created_at": user.created_at.isoformat().replace("+00:00", "Z"),
    }


def _serialize_tokens(token_pair: dict) -> dict:
    return {
        "access": token_pair["access"],
        "refresh": token_pair["refresh"],
        "expires_at": token_pair["expires_at"],
    }


# ---------------------------------------------------------------------------
# Auth services
# ---------------------------------------------------------------------------


def register(
    *,
    email: str,
    password: str,
    display_name: str,
    first_name: str = "",
    last_name: str = "",
    idempotency_key: str,
) -> dict:
    """Register a new user.

    Returns {"user": ..., "tokens": ...}.
    Raises ConflictError(AUTH_EMAIL_ALREADY_EXISTS) on duplicate email.
    """
    email = _normalize_email(email)
    _validate_email(email)
    _validate_password(password)

    with transaction.atomic():
        if User.objects.filter(email=email).exists():
            raise ConflictError(
                code="AUTH_EMAIL_ALREADY_EXISTS",
                message="An account with this email already exists.",
            )

        user = User.objects.create(
            email=email,
            password_hash=make_password(password),
            display_name=display_name,
            first_name=first_name,
            last_name=last_name,
        )

        UserPreferences.objects.create(user=user)
        KycProfile.objects.create(user=user)

        _emit_outbox(
            event_type="identity.UserRegistered",
            payload={
                "user_id": str(user.id),
                "email": user.email,
                "display_name": user.display_name,
                "actor_id": str(user.id),
                "occurred_at": _now_utc().isoformat(),
            },
            idempotency_key=f"user_registered:{user.id}",
            actor_id=str(user.id),
        )
        _record_audit(
            action="identity.register",
            actor_id=str(user.id),
            target_id=str(user.id),
            after_state={"email": email},
        )

    # Wallet creation outside user-creation tx to avoid coupling.
    _create_wallets(user_id=str(user.id))

    token_pair = issue_token_pair(user)
    return {
        "user": _serialize_user(user),
        "tokens": _serialize_tokens(token_pair),
    }


def login(
    *,
    email: str,
    password: str,
    device_label: str = "",
    ip_address: str | None = None,
    totp_code: str = "",
) -> dict:
    """Authenticate user and create a session.

    Returns {"user": ..., "tokens": ..., "session": ...}. When the account has
    two-factor enabled, a valid `totp_code` is also required.
    """
    email = _normalize_email(email)

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        raise AuthError(
            code="AUTH_INVALID_CREDENTIALS",
            message="Invalid email or password.",
        )

    # Auto-rehash legacy/migrated hashes to the preferred algorithm on success.
    if not check_password(password, user.password_hash, setter=_rehash_setter(user)):
        raise AuthError(
            code="AUTH_INVALID_CREDENTIALS",
            message="Invalid email or password.",
        )

    if not user.is_active:
        raise ForbiddenError(
            code="AUTH_ACCOUNT_DEACTIVATED",
            message="Your account has been deactivated.",
        )

    if user.two_factor_enabled:
        from . import totp

        if not totp_code:
            raise AuthError(code="AUTH_2FA_REQUIRED", message="A two-factor code is required.")
        if not totp.verify(user.totp_secret, totp_code):
            raise AuthError(code="AUTH_2FA_INVALID", message="Invalid two-factor code.")

    token_pair = issue_token_pair(user)
    refresh_jti = uuid.UUID(token_pair["refresh_jti"])

    with transaction.atomic():
        session = UserSession.objects.create(
            user=user,
            refresh_jti=refresh_jti,
            device_label=device_label,
            ip_address=ip_address,
        )

        _emit_outbox(
            event_type="identity.UserLoggedIn",
            payload={
                "user_id": str(user.id),
                "actor_id": str(user.id),
                "session_id": str(session.id),
                "occurred_at": _now_utc().isoformat(),
            },
            idempotency_key=f"user_logged_in:{session.id}",
            actor_id=str(user.id),
        )
        # Daily login reward — async via economy Outbox
        _emit_outbox(
            event_type="economy.DailyLoginRewardClaimRequested",
            payload={
                "user_id": str(user.id),
                "actor_id": str(user.id),
                "occurred_at": _now_utc().isoformat(),
            },
            idempotency_key=f"daily_login_claim:{user.id}:{_now_utc().date().isoformat()}",
            actor_id=str(user.id),
        )

    return {
        "user": _serialize_user(user),
        "tokens": _serialize_tokens(token_pair),
        "session": {
            "id": str(session.id),
            "device_label": session.device_label or None,
        },
    }


def refresh_token(*, refresh_jwt: str) -> dict:
    """Rotate the refresh token and issue a new pair.

    Returns {"tokens": ...}.
    """
    payload = _decode_refresh_token(refresh_jwt)
    jti = uuid.UUID(payload["jti"])
    user_id = payload["sub"]

    try:
        session = UserSession.objects.select_for_update().get(refresh_jti=jti)
    except UserSession.DoesNotExist:
        raise AuthError(code="AUTH_SESSION_REVOKED", message="Session has been revoked.")

    try:
        user = User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        raise AuthError(code="AUTH_INVALID_CREDENTIALS", message="User not found or inactive.")

    token_pair = issue_token_pair(user)
    new_jti = uuid.UUID(token_pair["refresh_jti"])

    with transaction.atomic():
        session.refresh_jti = new_jti
        session.last_used_at = _now_utc()
        session.save(update_fields=["refresh_jti", "last_used_at"])

    return {"tokens": _serialize_tokens(token_pair)}


def logout(*, refresh_jwt: str, user_id: str) -> None:
    """Delete the session associated with the refresh token."""
    try:
        payload = _decode_refresh_token(refresh_jwt)
    except AuthError:
        # Token invalid/expired — still attempt to delete any matching session.
        return

    jti_str = payload.get("jti")
    if not jti_str:
        return

    try:
        jti = uuid.UUID(jti_str)
    except ValueError:
        return

    UserSession.objects.filter(user_id=user_id, refresh_jti=jti).delete()


def get_me(*, user_id: str, session_id: str | None = None) -> dict:
    """Return the authenticated user's own profile (minimal view)."""
    try:
        user = User.objects.select_related("kyc_profile").get(id=user_id)
    except User.DoesNotExist:
        raise NotFoundError(code="USER_NOT_FOUND", message="User not found.")

    kyc_status = "not_submitted"
    try:
        kyc_status = user.kyc_profile.status
    except KycProfile.DoesNotExist:
        pass

    data = _serialize_user(user)
    data["kyc_status"] = kyc_status
    data["session_id"] = session_id
    return {"user": data}


def change_password(
    *,
    user_id: str,
    current_password: str,
    new_password: str,
    revoke_other_sessions: bool = False,
) -> None:
    """Change the user's password."""
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        raise NotFoundError(code="USER_NOT_FOUND", message="User not found.")

    if not check_password(current_password, user.password_hash):
        raise AuthError(code="AUTH_INVALID_CREDENTIALS", message="Current password is incorrect.")

    _validate_password(new_password)

    with transaction.atomic():
        user.password_hash = make_password(new_password)
        user.save(update_fields=["password_hash", "updated_at"])

        if revoke_other_sessions:
            UserSession.objects.filter(user=user).delete()

        _emit_outbox(
            event_type="identity.PasswordChanged",
            payload={
                "user_id": str(user.id),
                "actor_id": str(user.id),
                "occurred_at": _now_utc().isoformat(),
            },
            idempotency_key=f"password_changed:{user.id}:{uuid.uuid4().hex}",
            actor_id=str(user.id),
        )
        _record_audit(
            action="identity.change_password",
            actor_id=str(user.id),
            target_id=str(user.id),
            severity="notable",
        )


def request_password_reset(*, email: str) -> None:
    """Request a password reset.  Always returns None — no enumeration."""
    email = _normalize_email(email)

    try:
        user = User.objects.get(email=email, is_active=True)
    except User.DoesNotExist:
        return  # Prevent user enumeration

    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    expires_at = _now_utc() + timedelta(hours=1)

    with transaction.atomic():
        PasswordResetToken.objects.create(
            user=user,
            token_hash=token_hash,
            expires_at=expires_at,
        )

        _emit_outbox(
            event_type="identity.PasswordResetRequested",
            payload={
                "user_id": str(user.id),
                "actor_id": str(user.id),
                "reset_token": raw_token,  # NotificationService sends the link
                "occurred_at": _now_utc().isoformat(),
            },
            idempotency_key=f"password_reset_requested:{user.id}:{uuid.uuid4().hex}",
            actor_id=str(user.id),
        )


def confirm_password_reset(*, reset_token: str, new_password: str) -> None:
    """Consume a password reset token and update the password."""
    _validate_password(new_password)

    token_hash = _hash_token(reset_token)
    now = _now_utc()

    try:
        prt = PasswordResetToken.objects.select_related("user").get(token_hash=token_hash)
    except PasswordResetToken.DoesNotExist:
        raise ValidationError(code="AUTH_RESET_TOKEN_INVALID", message="Reset token is invalid.")

    if prt.used_at is not None:
        raise ValidationError(
            code="AUTH_RESET_TOKEN_INVALID", message="Reset token has already been used."
        )

    if prt.expires_at < now:
        raise ValidationError(code="AUTH_RESET_TOKEN_EXPIRED", message="Reset token has expired.")

    user = prt.user

    with transaction.atomic():
        user.password_hash = make_password(new_password)
        user.save(update_fields=["password_hash", "updated_at"])

        prt.used_at = now
        prt.save(update_fields=["used_at"])

        # Invalidate all sessions — forces re-login
        UserSession.objects.filter(user=user).delete()

        _emit_outbox(
            event_type="identity.PasswordChanged",
            payload={
                "user_id": str(user.id),
                "actor_id": str(user.id),
                "occurred_at": now.isoformat(),
            },
            idempotency_key=f"password_changed:{user.id}:{uuid.uuid4().hex}",
            actor_id=str(user.id),
        )
        _record_audit(
            action="identity.confirm_password_reset",
            actor_id=str(user.id),
            target_id=str(user.id),
            severity="notable",
        )


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------


def request_email_verification(*, user_id: str) -> None:
    """Issue an email-verification token for the user and emit the request event
    (NotificationService sends the link). No-op if already verified."""
    try:
        user = User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        raise NotFoundError(code="USER_NOT_FOUND", message="User not found.")
    if user.email_verified:
        return

    raw_token = secrets.token_urlsafe(32)
    expires_at = _now_utc() + timedelta(hours=24)
    with transaction.atomic():
        EmailVerificationToken.objects.create(
            user=user, token_hash=_hash_token(raw_token), expires_at=expires_at
        )
        _emit_outbox(
            event_type="identity.EmailVerificationRequested",
            payload={
                "user_id": str(user.id),
                "actor_id": str(user.id),
                "email": user.email,
                "verification_token": raw_token,  # NotificationService sends the link
                "occurred_at": _now_utc().isoformat(),
            },
            idempotency_key=f"email_verification_requested:{user.id}:{uuid.uuid4().hex}",
            actor_id=str(user.id),
        )


def confirm_email_verification(*, verification_token: str) -> dict:
    """Consume a verification token and flag the user's email verified."""
    token_hash = _hash_token(verification_token)
    now = _now_utc()

    try:
        evt = EmailVerificationToken.objects.select_related("user").get(token_hash=token_hash)
    except EmailVerificationToken.DoesNotExist:
        raise ValidationError(
            code="AUTH_VERIFICATION_TOKEN_INVALID", message="Verification token is invalid."
        )
    if evt.used_at is not None:
        raise ValidationError(
            code="AUTH_VERIFICATION_TOKEN_INVALID",
            message="Verification token has already been used.",
        )
    if evt.expires_at < now:
        raise ValidationError(
            code="AUTH_VERIFICATION_TOKEN_EXPIRED", message="Verification token has expired."
        )

    user = evt.user
    with transaction.atomic():
        evt.used_at = now
        evt.save(update_fields=["used_at"])
        if not user.email_verified:
            user.email_verified = True
            user.email_verified_at = now
            user.save(update_fields=["email_verified", "email_verified_at", "updated_at"])
            _emit_outbox(
                event_type="identity.EmailVerified",
                payload={
                    "user_id": str(user.id),
                    "actor_id": str(user.id),
                    "occurred_at": now.isoformat(),
                },
                idempotency_key=f"email_verified:{user.id}",
                actor_id=str(user.id),
            )
            _record_audit(
                action="identity.confirm_email_verification",
                actor_id=str(user.id),
                target_id=str(user.id),
                severity="info",
            )
    return {"email_verified": True}


# ---------------------------------------------------------------------------
# Two-factor authentication (TOTP)
# ---------------------------------------------------------------------------


def setup_two_factor(*, user_id: str) -> dict:
    """Generate a fresh TOTP secret for the user (not yet enabled) and return the
    secret + provisioning URI for an authenticator app."""
    from . import totp

    try:
        user = User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        raise NotFoundError(code="USER_NOT_FOUND", message="User not found.")
    if user.two_factor_enabled:
        raise ConflictError(
            code="AUTH_2FA_ALREADY_ENABLED", message="Two-factor is already enabled."
        )

    secret = totp.generate_secret()
    user.totp_secret = secret
    user.save(update_fields=["totp_secret", "updated_at"])
    return {
        "secret": secret,
        "otpauth_uri": totp.provisioning_uri(secret, account_name=user.email),
    }


def enable_two_factor(*, user_id: str, code: str) -> dict:
    """Verify a TOTP code against the pending secret and turn 2FA on."""
    from . import totp

    try:
        user = User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        raise NotFoundError(code="USER_NOT_FOUND", message="User not found.")
    if user.two_factor_enabled:
        raise ConflictError(
            code="AUTH_2FA_ALREADY_ENABLED", message="Two-factor is already enabled."
        )
    if not user.totp_secret:
        raise ValidationError(code="AUTH_2FA_NOT_SET_UP", message="Run two-factor setup first.")
    if not totp.verify(user.totp_secret, code):
        raise ValidationError(code="AUTH_2FA_INVALID", message="Invalid verification code.")

    with transaction.atomic():
        user.two_factor_enabled = True
        user.save(update_fields=["two_factor_enabled", "updated_at"])
        _emit_outbox(
            event_type="identity.TwoFactorEnabled",
            payload={"user_id": str(user.id), "occurred_at": _now_utc().isoformat()},
            idempotency_key=f"two_factor_enabled:{user.id}:{uuid.uuid4().hex}",
            actor_id=str(user.id),
        )
        _record_audit(
            action="identity.enable_two_factor",
            actor_id=str(user.id),
            target_id=str(user.id),
            severity="notable",
        )
    return {"two_factor_enabled": True}


def disable_two_factor(*, user_id: str, code: str) -> dict:
    """Disable 2FA after verifying a current code; clears the stored secret."""
    from . import totp

    try:
        user = User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        raise NotFoundError(code="USER_NOT_FOUND", message="User not found.")
    if not user.two_factor_enabled:
        raise ConflictError(code="AUTH_2FA_NOT_ENABLED", message="Two-factor is not enabled.")
    if not totp.verify(user.totp_secret, code):
        raise ValidationError(code="AUTH_2FA_INVALID", message="Invalid verification code.")

    with transaction.atomic():
        user.two_factor_enabled = False
        user.totp_secret = ""
        user.save(update_fields=["two_factor_enabled", "totp_secret", "updated_at"])
        _emit_outbox(
            event_type="identity.TwoFactorDisabled",
            payload={"user_id": str(user.id), "occurred_at": _now_utc().isoformat()},
            idempotency_key=f"two_factor_disabled:{user.id}:{uuid.uuid4().hex}",
            actor_id=str(user.id),
        )
        _record_audit(
            action="identity.disable_two_factor",
            actor_id=str(user.id),
            target_id=str(user.id),
            severity="notable",
        )
    return {"two_factor_enabled": False}


# ---------------------------------------------------------------------------
# Profile services
# ---------------------------------------------------------------------------


def _get_creator_profile_data(user: User) -> dict | None:
    if not user.is_creator:
        return None
    try:
        cp = user.creator_profile
        return {
            "user_id": str(user.id),
            "bio_extended": cp.bio_extended,
            "categories": cp.categories,
            "social_links": cp.social_links,
            "is_verified": cp.is_verified,
            "verified_at": cp.verified_at.isoformat().replace("+00:00", "Z")
            if cp.verified_at
            else None,
            "created_at": cp.created_at.isoformat().replace("+00:00", "Z"),
        }
    except CreatorProfile.DoesNotExist:
        return None


def get_profile(*, user_id: str) -> dict:
    """Full profile per contract §2."""
    try:
        user = User.objects.prefetch_related("creator_profile").get(id=user_id)
    except User.DoesNotExist:
        raise NotFoundError(code="USER_NOT_FOUND", message="User not found.")

    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "avatar_url": user.avatar_url,
        "bio": user.bio,
        "is_creator": user.is_creator,
        "is_seller": user.is_seller,
        "is_admin": user.is_admin,
        "email_verified": user.email_verified,
        "two_factor_enabled": user.two_factor_enabled,
        "follower_count": user.follower_count,
        "following_count": user.following_count,
        "creator_profile": _get_creator_profile_data(user),
        "stats": {
            "total_videos": 0,
            "total_dramas": 0,
            "total_likes_received": 0,
            "total_views_received": 0,
            "total_gifts_received_amount": "0.0000",
            "total_gifts_received_currency": "MP",
        },
        "created_at": user.created_at.isoformat().replace("+00:00", "Z"),
    }


def public_profiles(user_ids: list[str]) -> dict[str, dict]:
    """Batch-fetch minimal public profile cards keyed by str(user_id).

    Cross-app callers (commerce catalog, content owner cards) use this to avoid
    N+1 lookups. Returns only public-safe fields; missing users are omitted.
    """
    ids = {str(uid) for uid in user_ids if uid}
    if not ids:
        return {}
    users = User.objects.filter(id__in=ids).only("id", "display_name", "avatar_url", "is_creator")
    return {
        str(u.id): {
            "id": str(u.id),
            "display_name": u.display_name,
            "avatar_url": u.avatar_url,
            "is_creator": u.is_creator,
        }
        for u in users
    }


def follower_count(user_id: str) -> int:
    """Owner follower count for content viewer-context cards. 0 if user missing."""
    row = User.objects.filter(id=user_id).values_list("follower_count", flat=True).first()
    return int(row or 0)


def is_creator(user_id: str) -> bool:
    """Whether a user may publish content (creator flag). False if user missing."""
    return User.objects.filter(id=user_id, is_creator=True).exists()


def following_ids(follower_id: str | None, target_ids: list[str]) -> set[str]:
    """Subset of target_ids that follower_id follows. Batched for viewer_context
    (content owner-follow flags). Empty when unauthenticated."""
    ids = {str(t) for t in target_ids if t}
    if not follower_id or not ids:
        return set()
    rows = Follow.objects.filter(
        follower_id=follower_id,
        target_id__in=ids,  # type: ignore[misc]
    ).values_list("target_id", flat=True)
    return {str(t) for t in rows}


def mark_seller(*, user_id: str) -> None:
    """Flag a user as an approved seller. Idempotent; called by commerce on
    seller-application approval."""
    User.objects.filter(id=user_id).update(is_seller=True, updated_at=_now_utc())


def update_profile(*, user_id: str, **kwargs: Any) -> dict:
    """Partial profile update."""
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        raise NotFoundError(code="USER_NOT_FOUND", message="User not found.")

    allowed = {"display_name", "first_name", "last_name", "bio", "avatar_url"}
    updated_fields: list[str] = []
    for key, value in kwargs.items():
        if key in allowed and value is not None:
            setattr(user, key, value)
            updated_fields.append(key)

    if not updated_fields:
        return get_profile(user_id=user_id)

    updated_fields.append("updated_at")

    with transaction.atomic():
        user.save(update_fields=updated_fields)

        _emit_outbox(
            event_type="identity.ProfileUpdated",
            payload={
                "user_id": str(user.id),
                "actor_id": str(user.id),
                "fields": updated_fields,
                "occurred_at": _now_utc().isoformat(),
            },
            idempotency_key=f"profile_updated:{user.id}:{uuid.uuid4().hex}",
            actor_id=str(user.id),
        )

    return get_profile(user_id=user_id)


def get_preferences(*, user_id: str) -> dict:
    try:
        prefs, _ = UserPreferences.objects.get_or_create(user_id=user_id)
    except User.DoesNotExist:
        raise NotFoundError(code="USER_NOT_FOUND", message="User not found.")

    return {
        "language": prefs.language,
        "theme": prefs.theme,
        "timezone": prefs.timezone,
        "notifications": {
            "email_enabled": prefs.email_enabled,
            "push_enabled": prefs.push_enabled,
        },
    }


def update_preferences(*, user_id: str, **kwargs: Any) -> dict:
    try:
        prefs, _ = UserPreferences.objects.get_or_create(user_id=user_id)
    except User.DoesNotExist:
        raise NotFoundError(code="USER_NOT_FOUND", message="User not found.")

    allowed = {"language", "theme", "timezone", "email_enabled", "push_enabled"}
    updated_fields: list[str] = []
    for key, value in kwargs.items():
        if key in allowed and value is not None:
            setattr(prefs, key, value)
            updated_fields.append(key)

    if updated_fields:
        updated_fields.append("updated_at")
        prefs.save(update_fields=updated_fields)

    return get_preferences(user_id=user_id)


# ---------------------------------------------------------------------------
# Follow services
# ---------------------------------------------------------------------------


def follow_user(*, follower_id: str, target_id: str) -> dict:
    """Follow a user.  Idempotent — no-op if already following."""
    if follower_id == target_id:
        raise UnprocessableError(
            code="FOLLOW_SELF_FORBIDDEN", message="You cannot follow yourself."
        )

    try:
        target = User.objects.get(id=target_id, is_active=True)
    except User.DoesNotExist:
        raise NotFoundError(code="USER_NOT_FOUND", message="User not found.")

    try:
        follower = User.objects.get(id=follower_id)
    except User.DoesNotExist:
        raise NotFoundError(code="USER_NOT_FOUND", message="Follower not found.")

    with transaction.atomic():
        _follow, created = Follow.objects.get_or_create(  # type: ignore[misc]
            follower_id=follower_id,
            target_id=target_id,
        )

        if created:
            User.objects.filter(id=target_id).update(follower_count=target.follower_count + 1)
            User.objects.filter(id=follower_id).update(following_count=follower.following_count + 1)

            _emit_outbox(
                event_type="identity.UserFollowed",
                payload={
                    "user_id": str(target_id),
                    "follower_id": str(follower_id),
                    "actor_id": str(follower_id),
                    "occurred_at": _now_utc().isoformat(),
                },
                idempotency_key=f"user_followed:{_follow.id}",
                actor_id=str(follower_id),
            )

    target.refresh_from_db(fields=["follower_count"])
    return {
        "user_id": str(target_id),
        "is_following": True,
        "follower_count": target.follower_count,
    }


def unfollow_user(*, follower_id: str, target_id: str) -> None:
    """Unfollow a user.  Idempotent — no-op if not following."""
    try:
        target = User.objects.get(id=target_id)
    except User.DoesNotExist:
        raise NotFoundError(code="USER_NOT_FOUND", message="User not found.")

    try:
        follow = Follow.objects.get(follower_id=follower_id, target_id=target_id)  # type: ignore[misc]
    except Follow.DoesNotExist:
        return  # Already not following — idempotent

    with transaction.atomic():
        follow.delete()

        User.objects.filter(id=target_id, follower_count__gt=0).update(
            follower_count=target.follower_count - 1
        )
        try:
            follower = User.objects.get(id=follower_id)
            User.objects.filter(id=follower_id, following_count__gt=0).update(
                following_count=follower.following_count - 1
            )
        except User.DoesNotExist:
            pass

        _emit_outbox(
            event_type="identity.UserUnfollowed",
            payload={
                "user_id": str(target_id),
                "follower_id": str(follower_id),
                "actor_id": str(follower_id),
                "occurred_at": _now_utc().isoformat(),
            },
            idempotency_key=f"user_unfollowed:{follower_id}:{target_id}:{uuid.uuid4().hex}",
            actor_id=str(follower_id),
        )


# ---------------------------------------------------------------------------
# Public users
# ---------------------------------------------------------------------------


def get_public_user(*, viewer_id: str | None, user_id: str) -> dict:
    """Return public profile.  Viewer context attached when viewer_id provided."""
    try:
        user = User.objects.prefetch_related("creator_profile").get(id=user_id, is_active=True)
    except User.DoesNotExist:
        raise NotFoundError(code="USER_NOT_FOUND", message="User not found.")

    viewer_context: dict | None = None
    if viewer_id:
        is_following = Follow.objects.filter(  # type: ignore[misc]
            follower_id=viewer_id, target_id=user_id
        ).exists()
        viewer_context = {
            "is_following": is_following,
            "is_self": viewer_id == user_id,
        }

    return {
        "id": str(user.id),
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
        "bio": user.bio,
        "is_creator": user.is_creator,
        "creator_profile": _get_creator_profile_data(user),
        "follower_count": user.follower_count,
        "following_count": user.following_count,
        "viewer_context": viewer_context,
        "created_at": user.created_at.isoformat().replace("+00:00", "Z"),
    }


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def list_sessions(*, user_id: str, current_session_id: str | None = None) -> list:
    sessions = UserSession.objects.filter(user_id=user_id).order_by("-last_used_at")
    return [
        {
            "id": str(s.id),
            "device_label": s.device_label or None,
            "ip_address": s.ip_address,
            "last_used_at": s.last_used_at.isoformat().replace("+00:00", "Z"),
            "created_at": s.created_at.isoformat().replace("+00:00", "Z"),
            "is_current": str(s.id) == current_session_id,
        }
        for s in sessions
    ]


def revoke_session(*, user_id: str, session_id: str) -> None:
    try:
        session = UserSession.objects.get(id=session_id, user_id=user_id)
    except UserSession.DoesNotExist:
        raise NotFoundError(code="NOT_FOUND", message="Session not found.")
    session.delete()


# ---------------------------------------------------------------------------
# KYC services
# ---------------------------------------------------------------------------


def get_kyc(*, user_id: str) -> dict:
    """Return KYC profile, creating a default one if not yet present."""
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        raise NotFoundError(code="USER_NOT_FOUND", message="User not found.")

    profile, _ = KycProfile.objects.prefetch_related("documents").get_or_create(user=user)
    return _serialize_kyc_profile(profile)


def _serialize_kyc_profile(profile: KycProfile) -> dict:
    docs: dict[str, Any] = {"id_front": None, "selfie": None}
    for doc in profile.documents.all():
        docs[doc.document_type] = {
            "document_type": doc.document_type,
            "image_url": doc.image_url,
            "uploaded_at": doc.uploaded_at.isoformat().replace("+00:00", "Z"),
        }

    return {
        "status": profile.status,
        "full_name": profile.full_name or None,
        "date_of_birth": profile.date_of_birth.isoformat() if profile.date_of_birth else None,
        "nationality": profile.nationality or None,
        "id_type": profile.id_type or None,
        "id_number": profile.id_number or None,
        "id_expiry_date": profile.id_expiry_date.isoformat() if profile.id_expiry_date else None,
        "submitted_at": profile.submitted_at.isoformat().replace("+00:00", "Z")
        if profile.submitted_at
        else None,
        "reviewed_at": profile.reviewed_at.isoformat().replace("+00:00", "Z")
        if profile.reviewed_at
        else None,
        "reject_reason": profile.reject_reason or None,
        "documents": docs,
    }


def update_kyc(*, user_id: str, **kwargs: Any) -> dict:
    """Create/update KYC profile fields.  Resets to pending if previously approved."""
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        raise NotFoundError(code="USER_NOT_FOUND", message="User not found.")

    profile, _ = KycProfile.objects.get_or_create(user=user)

    was_approved = profile.status == "approved"
    allowed = {
        "full_name",
        "date_of_birth",
        "nationality",
        "id_type",
        "id_number",
        "id_expiry_date",
    }
    updated_fields: list[str] = []

    for key, value in kwargs.items():
        if key in allowed and value is not None:
            setattr(profile, key, value)
            updated_fields.append(key)

    if was_approved:
        profile.status = "pending"
        updated_fields.append("status")

    if updated_fields:
        updated_fields.append("updated_at")
        profile.save(update_fields=updated_fields)

        if was_approved:
            _emit_outbox(
                event_type="identity.KycResubmitted",
                payload={
                    "user_id": str(user.id),
                    "actor_id": str(user.id),
                    "occurred_at": _now_utc().isoformat(),
                },
                idempotency_key=f"kyc_resubmitted:{user.id}:{uuid.uuid4().hex}",
                actor_id=str(user.id),
            )

    profile.refresh_from_db()
    return _serialize_kyc_profile(profile)


def upload_kyc_document(*, user_id: str, document_type: str, image_url: str) -> dict:
    """Create or replace a KYC document."""
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        raise NotFoundError(code="USER_NOT_FOUND", message="User not found.")

    profile, _ = KycProfile.objects.get_or_create(user=user)

    doc, _ = KycDocument.objects.update_or_create(
        kyc_profile=profile,
        document_type=document_type,
        defaults={"image_url": image_url},
    )
    return {
        "document_type": doc.document_type,
        "image_url": doc.image_url,
        "uploaded_at": doc.uploaded_at.isoformat().replace("+00:00", "Z"),
    }


def submit_kyc(*, user_id: str) -> dict:
    """Finalize KYC submission.  Sets status=pending and emits event."""
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        raise NotFoundError(code="USER_NOT_FOUND", message="User not found.")

    try:
        profile = KycProfile.objects.prefetch_related("documents").get(user=user)
    except KycProfile.DoesNotExist:
        raise ValidationError(code="KYC_REQUIRED_FIELDS_MISSING", message="KYC profile not found.")

    required_fields = [
        "full_name",
        "date_of_birth",
        "nationality",
        "id_type",
        "id_number",
        "id_expiry_date",
    ]
    missing = [f for f in required_fields if not getattr(profile, f)]
    if missing:
        raise ValidationError(
            code="KYC_REQUIRED_FIELDS_MISSING",
            message="Required KYC fields are missing.",
            detail={"missing_fields": missing},
        )

    doc_types = {d.document_type for d in profile.documents.all()}
    missing_docs = [t for t in ("id_front", "selfie") if t not in doc_types]
    if missing_docs:
        raise ValidationError(
            code="KYC_REQUIRED_DOCUMENTS_MISSING",
            message="Required KYC documents are missing.",
            detail={"missing_documents": missing_docs},
        )

    with transaction.atomic():
        now = _now_utc()
        profile.status = "pending"
        profile.submitted_at = now
        profile.save(update_fields=["status", "submitted_at", "updated_at"])

        _emit_outbox(
            event_type="identity.KycSubmitted",
            payload={
                "user_id": str(user.id),
                "actor_id": str(user.id),
                "occurred_at": now.isoformat(),
            },
            idempotency_key=f"kyc_submitted:{user.id}:{uuid.uuid4().hex}",
            actor_id=str(user.id),
        )

    return _serialize_kyc_profile(profile)


# ---------------------------------------------------------------------------
# Creator profile services
# ---------------------------------------------------------------------------


def get_creator_profile(*, user_id: str) -> dict:
    try:
        user = User.objects.select_related("kyc_profile", "creator_profile").get(id=user_id)
    except User.DoesNotExist:
        raise NotFoundError(code="USER_NOT_FOUND", message="User not found.")

    if not user.is_creator:
        raise ForbiddenError(code="AUTH_FORBIDDEN", message="User is not a creator.")

    try:
        cp = user.creator_profile
    except CreatorProfile.DoesNotExist:
        raise NotFoundError(code="NOT_FOUND", message="Creator profile not found.")

    kyc_status = "not_submitted"
    try:
        kyc_status = user.kyc_profile.status
    except KycProfile.DoesNotExist:
        pass

    return {
        "user_id": str(user.id),
        "bio_extended": cp.bio_extended,
        "categories": cp.categories,
        "social_links": cp.social_links,
        "is_verified": cp.is_verified,
        "verified_at": cp.verified_at.isoformat().replace("+00:00", "Z")
        if cp.verified_at
        else None,
        "kyc_status": kyc_status,
        "created_at": cp.created_at.isoformat().replace("+00:00", "Z"),
    }


def update_creator_profile(*, user_id: str, **kwargs: Any) -> dict:
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        raise NotFoundError(code="USER_NOT_FOUND", message="User not found.")

    if not user.is_creator:
        raise ForbiddenError(code="AUTH_FORBIDDEN", message="User is not a creator.")

    cp, _ = CreatorProfile.objects.get_or_create(user=user)
    allowed = {"bio_extended", "categories", "social_links"}
    updated_fields: list[str] = []

    for key, value in kwargs.items():
        if key in allowed and value is not None:
            setattr(cp, key, value)
            updated_fields.append(key)

    if updated_fields:
        updated_fields.append("updated_at")
        cp.save(update_fields=updated_fields)

    return get_creator_profile(user_id=user_id)
