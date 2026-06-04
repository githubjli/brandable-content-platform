#!/usr/bin/env python
"""Entry point for the Notification gRPC service."""

from __future__ import annotations

import os
import sys

# Add the service directory to sys.path so sibling modules are importable.
_svc_dir = os.path.dirname(__file__)
sys.path.insert(0, _svc_dir)
# Add generated/ to sys.path so that the protoc-generated inter-package imports
# (e.g. `from notification.v1 import notification_pb2` inside _pb2_grpc.py) resolve.
sys.path.insert(0, os.path.join(_svc_dir, "generated"))

# Configure structured logging first (replaces basicConfig)
from logging_config import configure_logging  # noqa: E402

configure_logging()

import logging  # noqa: E402

logger = logging.getLogger(__name__)

# Set up OTel before grpc.server() is created so spans are captured from the start
from telemetry import setup_telemetry  # noqa: E402

setup_telemetry()

from concurrent import futures  # noqa: E402

import grpc  # noqa: E402

from interceptors import AuthInterceptor, TraceInterceptor  # noqa: E402
from servicer import NotificationServicer  # noqa: E402

try:
    from notification.v1 import notification_pb2_grpc  # type: ignore[import]
except ImportError as exc:
    logger.error(
        "Generated proto files not found. Run `make proto-gen` first. Error: %s", exc
    )
    sys.exit(1)


def serve(port: int = 50051, max_workers: int = 10) -> None:
    executor = futures.ThreadPoolExecutor(max_workers=max_workers)
    # Interceptors are applied inside-out: last in the list runs first.
    # AuthInterceptor is last so it is the outermost wrapper (runs first on
    # each incoming call), then TraceInterceptor opens the span.
    server = grpc.server(
        executor,
        interceptors=[TraceInterceptor(), AuthInterceptor()],
    )
    notification_pb2_grpc.add_NotificationServiceServicer_to_server(
        NotificationServicer(), server
    )
    listen_addr = f"[::]:{port}"
    server.add_insecure_port(listen_addr)
    server.start()
    logger.info("Notification gRPC server listening on %s", listen_addr, extra={"port": port})
    server.wait_for_termination()


if __name__ == "__main__":
    port = int(os.environ.get("GRPC_PORT", "50051"))
    serve(port=port)
