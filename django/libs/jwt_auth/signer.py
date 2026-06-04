"""JWT signing — private key side.

Only the Identity app uses this module.  All other apps use JWTAuthentication
(public key verification) from authentication.py.

RS256 private key is loaded from settings.JWT_PRIVATE_KEY_PATH.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import jwt
from django.conf import settings

if TYPE_CHECKING:
    from apps.identity.models import User

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_private_key() -> Any:
    """Load RSA private key from disk (cached for process lifetime)."""
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        with open(settings.JWT_PRIVATE_KEY_PATH, "rb") as f:
            return load_pem_private_key(f.read(), password=None)
    except FileNotFoundError:
        logger.warning(
            "JWT private key not found at %s — token signing will fail",
            settings.JWT_PRIVATE_KEY_PATH,
        )
        raise


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def sign_access_token(user_id: str, jti: str, scope: list[str]) -> str:
    """Sign an RS256 access JWT.

    Claims per ADR-0005: sub, iat, exp, jti, type, scope, aud, iss.
    TTL: settings.JWT_ACCESS_TTL_SECONDS (default 15 minutes).
    """
    private_key = _load_private_key()
    now = _now_utc()
    ttl_seconds = getattr(settings, "JWT_ACCESS_TTL_SECONDS", 15 * 60)
    exp = now + timedelta(seconds=ttl_seconds)

    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": exp,
        "jti": str(jti),
        "type": "access",
        "scope": scope,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_ISSUER,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def sign_refresh_token(user_id: str, jti: str) -> str:
    """Sign an RS256 refresh JWT.

    TTL: settings.JWT_REFRESH_TTL_SECONDS (default 7 days).
    """
    private_key = _load_private_key()
    now = _now_utc()
    ttl_seconds = getattr(settings, "JWT_REFRESH_TTL_SECONDS", 7 * 24 * 3600)
    exp = now + timedelta(seconds=ttl_seconds)

    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": exp,
        "jti": str(jti),
        "type": "refresh",
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_ISSUER,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def issue_token_pair(user: "User") -> dict:
    """Issue an access + refresh token pair for the given user.

    Returns::

        {
            "access":     "<jwt>",
            "refresh":    "<jwt>",
            "expires_at": "<ISO-8601 datetime>",
        }

    A fresh JTI is generated for each token.  The caller is responsible for
    persisting the refresh_jti in UserSession.
    """
    access_jti = str(uuid.uuid4())
    refresh_jti = str(uuid.uuid4())

    scope = ["default"]
    if user.is_admin:
        scope.append("admin")
    if user.is_creator:
        scope.append("creator")

    access_token = sign_access_token(
        user_id=str(user.id),
        jti=access_jti,
        scope=scope,
    )
    refresh_token = sign_refresh_token(
        user_id=str(user.id),
        jti=refresh_jti,
    )

    now = _now_utc()
    ttl_seconds = getattr(settings, "JWT_ACCESS_TTL_SECONDS", 15 * 60)
    expires_at = now + timedelta(seconds=ttl_seconds)

    return {
        "access": access_token,
        "refresh": refresh_token,
        "refresh_jti": refresh_jti,
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
    }
