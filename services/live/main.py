#!/usr/bin/env python
"""Entry point for the Live Runtime gRPC service."""

from __future__ import annotations

import os
import sys

_svc_dir = os.path.dirname(__file__)
sys.path.insert(0, _svc_dir)
sys.path.insert(0, os.path.join(_svc_dir, "generated"))

from logging_config import configure_logging  # noqa: E402

configure_logging()

import logging  # noqa: E402

logger = logging.getLogger(__name__)

from telemetry import setup_telemetry  # noqa: E402

setup_telemetry()

from concurrent import futures  # noqa: E402

import grpc  # noqa: E402

from interceptors import AuthInterceptor, TraceInterceptor  # noqa: E402
from servicer import LiveRuntimeServicer  # noqa: E402

try:
    from live.v1 import live_runtime_pb2_grpc  # type: ignore[import]
except ImportError as exc:
    logger.error("Generated proto files not found. Run `make proto-gen` first. Error: %s", exc)
    sys.exit(1)


def serve(port: int = 50053, max_workers: int = 10) -> None:
    executor = futures.ThreadPoolExecutor(max_workers=max_workers)
    server = grpc.server(executor, interceptors=[TraceInterceptor(), AuthInterceptor()])
    live_runtime_pb2_grpc.add_LiveRuntimeServiceServicer_to_server(LiveRuntimeServicer(), server)
    listen_addr = f"[::]:{port}"
    server.add_insecure_port(listen_addr)
    server.start()
    logger.info("Live Runtime gRPC server listening on %s", listen_addr, extra={"port": port})
    server.wait_for_termination()


if __name__ == "__main__":
    serve(port=int(os.environ.get("GRPC_PORT", "50053")))
