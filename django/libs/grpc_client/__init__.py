"""gRPC client helpers.

Each gRPC service has a dedicated channel/stub factory here.
All calls should include an explicit timeout (default: settings.GRPC_TIMEOUT_SECONDS).

Trace propagation
-----------------
Use get_metadata() to build gRPC call metadata that carries OTel trace context
(x-trace-id) and an optional request-id (x-request-id) so downstream services
can participate in the same trace.

Example::

    from django.libs.grpc_client import get_metadata, notification_channel
    from google.protobuf import empty_pb2

    with notification_channel() as channel:
        stub = NotificationServiceStub(channel)
        resp = stub.Ping(
            empty_pb2.Empty(),
            metadata=get_metadata(),
            timeout=settings.GRPC_TIMEOUT_SECONDS,
        )
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager

import grpc
from django.conf import settings

logger = logging.getLogger(__name__)


def get_metadata(request_id: str | None = None) -> list[tuple[str, str]]:
    """Build gRPC call metadata with OTel trace context.

    Injects:
    - ``x-trace-id``: current OTel trace ID hex string (if an active span exists)
    - ``x-request-id``: caller-supplied or Django request-id (if available)

    The metadata list can be passed directly to any gRPC stub call via the
    ``metadata=`` keyword argument.
    """
    metadata: list[tuple[str, str]] = []

    # Inject OTel W3C traceparent + tracestate propagation headers
    try:
        from opentelemetry import propagate, trace

        span = trace.get_current_span()
        ctx = span.get_span_context()

        if ctx and ctx.is_valid:
            trace_id_hex = format(ctx.trace_id, "032x")
            metadata.append(("x-trace-id", trace_id_hex))

        # Also propagate W3C traceparent so downstream can continue the trace
        carrier: dict[str, str] = {}
        propagate.inject(carrier)
        for key, value in carrier.items():
            metadata.append((key.lower(), value))
    except Exception:
        pass  # OTel not installed or no active span — silently skip

    if request_id:
        metadata.append(("x-request-id", request_id))

    return metadata


@contextmanager
def notification_channel(
    metadata: list[tuple[str, str]] | None = None,
) -> Generator:
    """Context manager that yields a gRPC channel to the Notification service.

    Args:
        metadata: Optional metadata to attach to every call on this channel.
                  Prefer passing metadata per-call via stub methods for clarity.
                  This parameter is kept for backward compatibility.
    """
    address = settings.GRPC_NOTIFICATION_ADDRESS
    channel = grpc.insecure_channel(address)
    try:
        yield channel
    finally:
        channel.close()


def get_notification_stub():
    """Return a (stub, channel) pair for the NotificationService.

    Usage::

        stub, channel = get_notification_stub()
        try:
            resp = stub.Ping(
                empty_pb2.Empty(),
                metadata=get_metadata(),
                timeout=settings.GRPC_TIMEOUT_SECONDS,
            )
        finally:
            channel.close()
    """
    try:
        import os
        import sys

        # Fall back to generated stubs bundled with the Django client library
        _client_generated = os.path.join(os.path.dirname(__file__), "generated")
        if _client_generated not in sys.path:
            sys.path.insert(0, _client_generated)

        try:
            from generated import notification_pb2_grpc
        except ImportError:
            # Try local generated dir inside grpc_client
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "notification_pb2_grpc",
                os.path.join(_client_generated, "notification_pb2_grpc.py"),
            )
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                notification_pb2_grpc = mod
            else:
                raise

        channel = grpc.insecure_channel(settings.GRPC_NOTIFICATION_ADDRESS)
        stub = notification_pb2_grpc.NotificationServiceStub(channel)
        return stub, channel
    except ImportError as exc:
        raise RuntimeError(
            f"Notification proto stubs not available: {exc}. Run 'make proto-gen'."
        ) from exc


def _notification_modules() -> tuple:
    """Import the generated notification pb2 + grpc modules (generated/ on path)."""
    import os
    import sys

    generated = os.path.join(os.path.dirname(__file__), "generated")
    if generated not in sys.path:
        sys.path.insert(0, generated)
    from notification.v1 import notification_pb2, notification_pb2_grpc

    return notification_pb2, notification_pb2_grpc


def send_notification(
    *,
    idempotency_key: str,
    channel: str,
    template_code: str,
    recipient_user_id: str = "",
    recipient_address: str = "",
    variables: dict[str, str] | None = None,
    timeout: float | None = None,
) -> dict[str, str]:
    """Call NotificationService.Send. Returns {"status", "message_id"}.

    Raises grpc.RpcError if the service is unreachable — callers (event handlers)
    decide whether to retry. Trace context propagates via get_metadata().
    """
    notification_pb2, notification_pb2_grpc = _notification_modules()
    request = notification_pb2.SendRequest(
        idempotency_key=idempotency_key,
        channel=channel,
        template_code=template_code,
        recipient_user_id=recipient_user_id,
        recipient_address=recipient_address,
        variables=variables or {},
    )
    grpc_channel = grpc.insecure_channel(settings.GRPC_NOTIFICATION_ADDRESS)
    try:
        stub = notification_pb2_grpc.NotificationServiceStub(grpc_channel)
        response = stub.Send(
            request,
            metadata=get_metadata(),
            timeout=timeout or settings.GRPC_TIMEOUT_SECONDS,
        )
        return {"status": response.status, "message_id": response.message_id}
    finally:
        grpc_channel.close()
