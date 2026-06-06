"""gRPC server interceptors for the Live Runtime service.

Two interceptors are provided:
- TraceInterceptor: extracts OTel trace context from metadata and creates a server span.
- AuthInterceptor: validates RS256 Bearer JWT; skips auth for health-check methods.

Wire order in main.py:
    server = grpc.server(executor, interceptors=[TraceInterceptor(), AuthInterceptor()])
The list is applied inside-out, so AuthInterceptor runs first (outermost).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

import grpc
import jwt

logger = logging.getLogger(__name__)

# Methods that skip JWT authentication (case-insensitive suffix match on full method path)
_NO_AUTH_METHODS: frozenset[str] = frozenset(["ping"])


def _method_name(handler_call_details: Any) -> str:
    """Extract the bare RPC method name from the full method path.

    Full path looks like: /live.v1.LiveRuntimeService/Ping
    """
    method = handler_call_details.method or ""
    return method.rsplit("/", 1)[-1].lower()


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

_cached_public_key: Any = None


def _load_public_key() -> Any:
    global _cached_public_key
    if _cached_public_key is not None:
        return _cached_public_key

    key_path = os.environ.get("JWT_PUBLIC_KEY_PATH", "")
    if not key_path:
        logger.warning("JWT_PUBLIC_KEY_PATH not set — JWT auth disabled")
        return None

    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        with open(key_path, "rb") as f:
            _cached_public_key = load_pem_public_key(f.read())
        logger.info("JWT public key loaded from %s", key_path)
        return _cached_public_key
    except FileNotFoundError:
        logger.warning("JWT public key not found at %s — JWT auth disabled", key_path)
        return None


def _decode_jwt(token: str) -> dict:
    """Decode and validate an RS256 JWT. Returns the payload on success."""
    public_key = _load_public_key()
    if public_key is None:
        raise ValueError("JWT public key not configured")

    issuer = os.environ.get("JWT_ISSUER", "")
    options: dict = {"verify_aud": False}
    if not issuer:
        options["verify_iss"] = False

    payload = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        issuer=issuer if issuer else None,
        options=options,
    )
    return payload


# ---------------------------------------------------------------------------
# AuthInterceptor
# ---------------------------------------------------------------------------


class AuthInterceptor(grpc.ServerInterceptor):
    """Validate RS256 Bearer JWT on every call except those in _NO_AUTH_METHODS."""

    def intercept_service(
        self,
        continuation: Callable,
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        handler = continuation(handler_call_details)
        if handler is None:
            return handler  # type: ignore[return-value]

        method = _method_name(handler_call_details)
        if method in _NO_AUTH_METHODS:
            return handler

        # Wrap the actual handler to perform auth before calling it
        return _wrap_handler_with_auth(handler)


def _wrap_handler_with_auth(handler: grpc.RpcMethodHandler) -> grpc.RpcMethodHandler:
    """Return a new RpcMethodHandler that validates JWT before delegating."""

    def _auth_wrapper(request_or_iterator: Any, context: grpc.ServicerContext) -> Any:
        token = _extract_bearer_token(context)
        if token is None:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Missing Authorization metadata")
            return

        try:
            payload = _decode_jwt(token)
        except jwt.ExpiredSignatureError:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Token has expired")
            return
        except jwt.InvalidTokenError as exc:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, f"Invalid token: {exc}")
            return
        except ValueError as exc:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, str(exc))
            return

        token_type = payload.get("type")
        if token_type not in ("access", "service"):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Token type must be 'access' or 'service'")
            return

        user_id = payload.get("sub", "")

        # Attach claims to invocation metadata so servicers can read them
        existing: list[tuple[str, str]] = list(context.invocation_metadata())
        existing.append(("x-auth-user-id", user_id))
        existing.append(("x-auth-token-type", token_type))
        # gRPC Python doesn't support mutating invocation_metadata; attach as
        # well-known attributes on the context object for servicer access.
        context._auth_user_id = user_id  # type: ignore[attr-defined]
        context._auth_token_type = token_type  # type: ignore[attr-defined]

        if handler.unary_unary:
            return handler.unary_unary(request_or_iterator, context)
        if handler.unary_stream:
            return handler.unary_stream(request_or_iterator, context)
        if handler.stream_unary:
            return handler.stream_unary(request_or_iterator, context)
        if handler.stream_stream:
            return handler.stream_stream(request_or_iterator, context)

    # Rebuild handler preserving the original call type
    if handler.unary_unary:
        return grpc.unary_unary_rpc_method_handler(
            _auth_wrapper,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
    if handler.unary_stream:
        return grpc.unary_stream_rpc_method_handler(
            _auth_wrapper,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
    if handler.stream_unary:
        return grpc.stream_unary_rpc_method_handler(
            _auth_wrapper,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
    if handler.stream_stream:
        return grpc.stream_stream_rpc_method_handler(
            _auth_wrapper,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
    return handler  # generic / unrecognised


def _extract_bearer_token(context: grpc.ServicerContext) -> str | None:
    """Extract Bearer token from gRPC invocation metadata."""
    for key, value in context.invocation_metadata():
        if key.lower() == "authorization":
            parts = value.split(" ", 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                return parts[1]
            return None
    return None


# ---------------------------------------------------------------------------
# TraceInterceptor
# ---------------------------------------------------------------------------


class TraceInterceptor(grpc.ServerInterceptor):
    """Extract OTel trace context from metadata and create a server span per RPC."""

    def intercept_service(
        self,
        continuation: Callable,
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        handler = continuation(handler_call_details)
        if handler is None:
            return handler  # type: ignore[return-value]

        full_method: str = handler_call_details.method or "unknown"
        return _wrap_handler_with_trace(handler, full_method)


def _wrap_handler_with_trace(
    handler: grpc.RpcMethodHandler,
    full_method: str,
) -> grpc.RpcMethodHandler:
    """Return a handler that opens an OTel span around the RPC."""

    # Derive a human-readable span name from the full method path
    # e.g. /live.v1.LiveRuntimeService/Ping -> live.v1.LiveRuntimeService/Ping
    span_name = full_method.lstrip("/")

    def _trace_wrapper(request_or_iterator: Any, context: grpc.ServicerContext) -> Any:
        try:
            from opentelemetry import propagate, trace
            from opentelemetry.trace import SpanKind, StatusCode
        except ImportError:
            # OTel not installed; fall through without tracing
            return _invoke_handler(handler, request_or_iterator, context)

        # Build a carrier dict from invocation metadata
        metadata_dict: dict[str, str] = {
            k: v for k, v in context.invocation_metadata()
        }

        ctx = propagate.extract(metadata_dict)
        tracer = trace.get_tracer("live-service")
        trace_id_str = metadata_dict.get("x-trace-id", "")

        with tracer.start_as_current_span(
            span_name,
            context=ctx,
            kind=SpanKind.SERVER,
        ) as span:
            if trace_id_str:
                span.set_attribute("trace_id", trace_id_str)
            span.set_attribute("rpc.method", span_name)

            try:
                result = _invoke_handler(handler, request_or_iterator, context)
                span.set_status(StatusCode.OK)
                return result
            except grpc.RpcError as exc:
                span.set_status(StatusCode.ERROR, str(exc))
                raise
            except Exception as exc:
                span.set_status(StatusCode.ERROR, str(exc))
                raise

    if handler.unary_unary:
        return grpc.unary_unary_rpc_method_handler(
            _trace_wrapper,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
    if handler.unary_stream:
        return grpc.unary_stream_rpc_method_handler(
            _trace_wrapper,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
    if handler.stream_unary:
        return grpc.stream_unary_rpc_method_handler(
            _trace_wrapper,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
    if handler.stream_stream:
        return grpc.stream_stream_rpc_method_handler(
            _trace_wrapper,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
    return handler


def _invoke_handler(
    handler: grpc.RpcMethodHandler,
    request_or_iterator: Any,
    context: grpc.ServicerContext,
) -> Any:
    if handler.unary_unary:
        return handler.unary_unary(request_or_iterator, context)
    if handler.unary_stream:
        return handler.unary_stream(request_or_iterator, context)
    if handler.stream_unary:
        return handler.stream_unary(request_or_iterator, context)
    if handler.stream_stream:
        return handler.stream_stream(request_or_iterator, context)
    raise RuntimeError("Unknown handler type")
