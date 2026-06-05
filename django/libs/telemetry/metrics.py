"""Prometheus metrics endpoint at /internal/metrics.

Exposes basic Django process metrics via opentelemetry-exporter-prometheus
if available, otherwise returns a minimal text/plain response so the
endpoint always works.

Conventions §1: internal surface, not authenticated.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.views import View


class MetricsView(View):
    """GET /internal/metrics — Prometheus text exposition format."""

    def get(self, request: HttpRequest) -> HttpResponse:
        try:
            from prometheus_client import (
                CONTENT_TYPE_LATEST,
                generate_latest,
            )

            output = generate_latest()
            return HttpResponse(output, content_type=CONTENT_TYPE_LATEST)
        except ImportError:
            pass

        # Fallback: minimal response so monitoring probes don't error
        return HttpResponse(
            "# Prometheus client not installed\n# pip install prometheus-client\n",
            content_type="text/plain; version=0.0.4",
        )
