"""OpenTelemetry setup for brandable-content-platform."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_telemetry_configured = False


def setup_telemetry() -> None:
    """Configure OTel SDK. Called once from PlatformConfigConfig.ready()."""
    global _telemetry_configured

    if _telemetry_configured:
        return

    enabled = os.environ.get("OTEL_ENABLED", "true").lower() == "true"
    if not enabled:
        logger.debug("OpenTelemetry disabled (OTEL_ENABLED != true)")
        _telemetry_configured = True
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.django import DjangoInstrumentor
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        service_name = os.environ.get("OTEL_SERVICE_NAME", "brandable-content-platform")
        otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))

        trace.set_tracer_provider(provider)

        DjangoInstrumentor().instrument()
        RedisInstrumentor().instrument()

        # psycopg3 instrumentation
        try:
            from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
            PsycopgInstrumentor().instrument()
        except ImportError:
            logger.debug("psycopg OTel instrumentation not available")

        logger.info("OpenTelemetry configured", extra={"otlp_endpoint": otlp_endpoint})

    except Exception as exc:
        # OTel setup should never crash the app
        logger.warning("OpenTelemetry setup failed: %s", exc)

    _telemetry_configured = True


def get_trace_id() -> str:
    """Return the current OTel trace ID as a hex string, or empty string."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.is_valid:
            return format(ctx.trace_id, "032x")
    except Exception:
        pass
    return ""
