"""JWKS endpoint — returns the public key(s) used to verify JWT tokens."""

from __future__ import annotations

import base64
import logging

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views import View

logger = logging.getLogger(__name__)


class JWKSView(View):
    """GET /.well-known/jwks.json — public key set."""

    def get(self, request: HttpRequest) -> JsonResponse:
        try:
            from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
            from cryptography.hazmat.primitives.serialization import (
                load_pem_public_key,
            )

            with open(settings.JWT_PUBLIC_KEY_PATH, "rb") as f:
                pub_key = load_pem_public_key(f.read())

            if not isinstance(pub_key, RSAPublicKey):
                raise ValueError("Only RSA keys supported")

            pub_numbers = pub_key.public_key().public_numbers() if hasattr(pub_key, "public_key") else pub_key.public_numbers()

            def _int_to_base64url(n: int) -> str:
                length = (n.bit_length() + 7) // 8
                return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()

            jwk = {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": settings.JWT_KID,
                "n": _int_to_base64url(pub_numbers.n),
                "e": _int_to_base64url(pub_numbers.e),
            }
            response = JsonResponse({"keys": [jwk]})
            response["Cache-Control"] = "public, max-age=3600"
            response["Vary"] = "Accept"
            return response

        except FileNotFoundError:
            logger.warning("JWT public key not found at %s", settings.JWT_PUBLIC_KEY_PATH)
            return JsonResponse({"keys": []})
        except Exception as exc:
            logger.error("JWKS generation failed: %s", exc)
            return JsonResponse({"keys": []}, status=500)
