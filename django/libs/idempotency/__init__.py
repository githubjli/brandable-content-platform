"""Idempotency key enforcement for write endpoints.

Money-touching endpoints MUST require Idempotency-Key header (see contracts/conventions.md §3).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from functools import wraps

from django.core.cache import cache
from rest_framework.request import Request
from rest_framework.response import Response

logger = logging.getLogger(__name__)

IDEMPOTENCY_HEADER = "HTTP_IDEMPOTENCY_KEY"
IDEMPOTENCY_CACHE_TTL = 24 * 3600  # 24 hours
MAX_KEY_LENGTH = 128


def idempotent(view_func: Callable) -> Callable:
    """Decorator that enforces idempotency on a DRF view.

    Works on both:
    - @api_view function views: (request, *args, **kwargs)
    - APIView method views:     (self, request, *args, **kwargs)

    Returns the cached response if the same Idempotency-Key has been seen
    within 24 hours. Otherwise executes the view and caches the result.
    """

    @wraps(view_func)
    def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
        # Detect APIView method (first arg is `self`, second is `request`)
        # vs plain function view (first arg is `request`).
        from rest_framework.views import APIView

        if args and isinstance(args[0], APIView):
            _self, request, *rest = args
            call = lambda: view_func(_self, request, *rest, **kwargs)  # noqa: E731
        else:
            request, *rest = args
            call = lambda: view_func(request, *rest, **kwargs)  # noqa: E731

        idempotency_key = request.META.get(IDEMPOTENCY_HEADER) or request.headers.get(
            "Idempotency-Key"
        )

        if not idempotency_key:
            from rest_framework import status as drf_status

            return Response(
                {
                    "error": {
                        "code": "VALIDATION_IDEMPOTENCY_KEY_REQUIRED",
                        "message": "Idempotency-Key header is required.",
                    }
                },
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        if len(idempotency_key) > MAX_KEY_LENGTH:
            from rest_framework import status as drf_status

            return Response(
                {
                    "error": {
                        "code": "VALIDATION_IDEMPOTENCY_KEY_TOO_LONG",
                        "message": f"Idempotency-Key must be <= {MAX_KEY_LENGTH} characters.",
                    }
                },
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        cache_key = _make_cache_key(request, idempotency_key)
        cached = cache.get(cache_key)
        if cached is not None:
            logger.debug("Idempotency cache hit for key %s", idempotency_key)
            return Response(cached["data"], status=cached["status"])

        response = call()

        if hasattr(response, "data") and response.status_code < 500:
            cache.set(
                cache_key,
                {"data": response.data, "status": response.status_code},
                IDEMPOTENCY_CACHE_TTL,
            )

        return response

    return wrapper


def _make_cache_key(request: Request, idempotency_key: str) -> str:
    user_id = getattr(getattr(request, "user", None), "id", "anon")
    raw = f"idempotency:{user_id}:{request.path}:{idempotency_key}"
    return hashlib.sha256(raw.encode()).hexdigest()
