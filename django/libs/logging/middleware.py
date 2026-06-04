"""Request logging middleware.

Logs every request with method, path, status code, duration, and trace_id.
Attaches request_id to the OTel span as an attribute.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

logger = logging.getLogger("brandable.requests")

REQUEST_ID_HEADER = "X-Request-Id"


class RequestLoggingMiddleware:
    """Log each request; inject request_id into response headers and log context."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        request.request_id = request_id  # type: ignore[attr-defined]

        # Attach to current OTel span so it appears in traces
        try:
            from opentelemetry import trace

            span = trace.get_current_span()
            span.set_attribute("http.request_id", request_id)
        except Exception:
            pass

        started = time.monotonic()
        response = self.get_response(request)
        duration_ms = (time.monotonic() - started) * 1000

        response[REQUEST_ID_HEADER] = request_id

        logger.info(
            "%s %s %s",
            request.method,
            request.path,
            response.status_code,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "duration_ms": round(duration_ms, 2),
            },
        )
        return response
