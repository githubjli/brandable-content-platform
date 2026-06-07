"""Minimal TOTP (RFC 6238) — stdlib only, no third-party dependency.

SHA-1, 6 digits, 30-second step (the de-facto standard authenticator apps use).
Verification accepts the adjacent steps (±1) to tolerate small clock skew.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

_DIGITS = 6
_STEP = 30
_SKEW_STEPS = 1


def generate_secret() -> str:
    """Return a base32 secret (no padding) suitable for authenticator apps."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _code_at(secret: str, counter: int) -> str:
    # base32 decode (re-pad to a multiple of 8).
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**_DIGITS)).zfill(_DIGITS)


def verify(secret: str, code: str, *, at: float | None = None) -> bool:
    """Whether `code` matches the secret within the skew window."""
    if not secret or not code or not code.strip().isdigit():
        return False
    code = code.strip()
    counter = int((at if at is not None else time.time()) // _STEP)
    for delta in range(-_SKEW_STEPS, _SKEW_STEPS + 1):
        if hmac.compare_digest(_code_at(secret, counter + delta), code):
            return True
    return False


def provisioning_uri(secret: str, *, account_name: str, issuer: str = "Brandable") -> str:
    """otpauth:// URI for QR provisioning in authenticator apps."""
    label = quote(f"{issuer}:{account_name}")
    params = f"secret={secret}&issuer={quote(issuer)}&digits={_DIGITS}&period={_STEP}"
    return f"otpauth://totp/{label}?{params}"
