#!/usr/bin/env python
"""Integration smoke test: can we call Ping on the chat service?

Run with:
    python services/chat/test_ping.py

Connects to the Chat gRPC service, calls Ping, and asserts a version is returned.
Set GRPC_CHAT_ADDRESS to override the default localhost:50052.
"""

from __future__ import annotations

import os
import sys

_service_dir = os.path.dirname(__file__)
for path in (_service_dir, os.path.join(_service_dir, "generated")):
    if path not in sys.path:
        sys.path.insert(0, path)

import grpc

try:
    from chat.v1 import chat_pb2, chat_pb2_grpc  # type: ignore[import]
except ImportError as exc:
    print(f"ERROR: Generated proto stubs not found: {exc}. Run `make proto-gen`.")
    sys.exit(1)


def run_ping(address: str) -> None:
    print(f"Connecting to Chat service at {address} ...")
    channel = grpc.insecure_channel(address)
    stub = chat_pb2_grpc.ChatServiceStub(channel)
    try:
        response = stub.Ping(chat_pb2.PingRequest(), timeout=5.0)
    except grpc.RpcError as exc:
        print(f"FAIL: gRPC call failed — {exc.code()}: {exc.details()}")
        sys.exit(1)
    finally:
        channel.close()

    assert response.version, "FAIL: expected a non-empty version"
    print(f"OK: Ping returned version={response.version!r}")


if __name__ == "__main__":
    run_ping(os.environ.get("GRPC_CHAT_ADDRESS", "localhost:50052"))
