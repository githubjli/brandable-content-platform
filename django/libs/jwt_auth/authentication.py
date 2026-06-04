"""DRF JWT Authentication using RS256."""

from __future__ import annotations

import logging
from typing import Any

import jwt
from django.conf import settings
from rest_framework import authentication, exceptions

logger = logging.getLogger(__name__)


def _load_public_key() -> Any:
    """Load RSA public key from disk (cached on first call)."""
    try:
        from cryptography.hazmat.primitives.serialization import (
            load_pem_public_key,
        )

        with open(settings.JWT_PUBLIC_KEY_PATH, "rb") as f:
            return load_pem_public_key(f.read())
    except FileNotFoundError:
        logger.warning(
            "JWT public key not found at %s — JWT auth disabled",
            settings.JWT_PUBLIC_KEY_PATH,
        )
        return None


_cached_public_key: Any = None


def get_public_key() -> Any:
    global _cached_public_key
    if _cached_public_key is None:
        _cached_public_key = _load_public_key()
    return _cached_public_key


class JWTAuthentication(authentication.BaseAuthentication):
    """Authenticate requests with a Bearer RS256 JWT.

    Returns (AnonymousLikeUser, token_payload) on success so views can
    access the raw claims.  Returns None (anonymous) if no Authorization
    header is present.  Raises AuthenticationFailed on malformed/invalid
    tokens.
    """

    www_authenticate_realm = "api"

    def authenticate(self, request: Any) -> tuple | None:
        auth_header = authentication.get_authorization_header(request).decode(
            "utf-8", errors="replace"
        )
        if not auth_header:
            return None

        parts = auth_header.split()
        if parts[0].lower() != "bearer":
            return None
        if len(parts) != 2:
            raise exceptions.AuthenticationFailed("Invalid Authorization header format.")

        token = parts[1]
        return self._decode(token)

    def _decode(self, token: str) -> tuple:
        public_key = get_public_key()
        if public_key is None:
            raise exceptions.AuthenticationFailed("JWT public key not configured.")

        try:
            payload = jwt.decode(
                token,
                public_key,
                algorithms=[settings.JWT_ALGORITHM],
                issuer=settings.JWT_ISSUER,
                options={"verify_aud": False},  # aud validated separately when set
            )
        except jwt.ExpiredSignatureError as err:
            raise exceptions.AuthenticationFailed("Token has expired.") from err
        except jwt.InvalidTokenError as exc:
            raise exceptions.AuthenticationFailed(f"Invalid token: {exc}") from exc

        if payload.get("type") not in ("access", "service"):
            raise exceptions.AuthenticationFailed("Token type must be 'access' or 'service'.")

        user = JWTUser(payload)
        return (user, payload)

    def authenticate_header(self, request: Any) -> str:
        return f'Bearer realm="{self.www_authenticate_realm}"'


class JWTUser:
    """Minimal user-like object built from JWT payload.

    The real User ORM object is loaded lazily by views that need it.
    """

    is_authenticated = True
    is_anonymous = False

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.id = payload.get("sub")
        self.pk = self.id

    def __str__(self) -> str:
        return f"JWTUser({self.id})"
