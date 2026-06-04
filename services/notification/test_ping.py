#!/usr/bin/env python
"""Integration smoke test: can we call Ping on the notification service?

Run with:
    python services/notification/test_ping.py

The test connects to the Notification gRPC service, calls Ping, and asserts
that the response message is "pong".  Set GRPC_NOTIFICATION_ADDRESS to
override the default localhost:50051.
"""

from __future__ import annotations

import os
import sys

# Make sure the generated stubs can be imported when running from the repo root
_service_dir = os.path.dirname(__file__)
if _service_dir not in sys.path:
    sys.path.insert(0, _service_dir)

import grpc

try:
    from generated import notification_pb2, notification_pb2_grpc  # type: ignore[import]
except ImportError as exc:
    print(f"ERROR: Generated proto stubs not found: {exc}")
    print("Run `make proto-gen` first.")
    sys.exit(1)

try:
    from google.protobuf import empty_pb2
except ImportError as exc:
    print(f"ERROR: google.protobuf not installed: {exc}")
    sys.exit(1)


def run_ping(address: str) -> None:
    print(f"Connecting to Notification service at {address} ...")
    channel = grpc.insecure_channel(address)
    stub = notification_pb2_grpc.NotificationServiceStub(channel)

    try:
        response = stub.Ping(empty_pb2.Empty(), timeout=5.0)
    except grpc.RpcError as exc:
        print(f"FAIL: gRPC call failed — {exc.code()}: {exc.details()}")
        sys.exit(1)
    finally:
        channel.close()

    assert response.message == "pong", (
        f"FAIL: expected response.message == 'pong', got {response.message!r}"
    )
    print(f"OK: Ping returned message={response.message!r}")


if __name__ == "__main__":
    address = os.environ.get("GRPC_NOTIFICATION_ADDRESS", "localhost:50051")
    run_ping(address)
