"""OpenTelemetry setup for the Notification gRPC service.

Call setup_telemetry() once at process start, before grpc.server() is created.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_telemetry_configured = False


def setup_telemetry() -> None:
    """Configure the OTel SDK with an OTLP gRPC exporter."""
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
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        service_name = os.environ.get("OTEL_SERVICE_NAME", "notification-service")
        otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))

        trace.set_tracer_provider(provider)

        # Instrument gRPC client calls made by this service (e.g. to other services)
        try:
            from opentelemetry.instrumentation.grpc import GrpcInstrumentorClient
            GrpcInstrumentorClient().instrument()
        except ImportError:
            logger.debug("opentelemetry-instrumentation-grpc not available; client instrumentation skipped")

        logger.info("OpenTelemetry configured for %s → %s", service_name, otlp_endpoint)

    except Exception as exc:
        # OTel setup must never crash the service
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
