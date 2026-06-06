#!/usr/bin/env python
"""Unit test for ChatServicer (no running server needed).

Run with:
    python services/chat/test_servicer.py

Asserts Ping returns a version, and that a not-yet-implemented RPC aborts with
UNIMPLEMENTED.
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
    from chat.v1 import chat_pb2  # type: ignore[import]
except ImportError as exc:
    print(f"ERROR: Generated proto stubs not found: {exc}. Run `make proto-gen`.")
    sys.exit(1)

from servicer import ChatServicer  # noqa: E402


class _AbortError(Exception):
    def __init__(self, code, details):
        self.code = code
        self.details = details


class _FakeContext:
    def abort(self, code, details):
        raise _AbortError(code, details)


def run() -> None:
    servicer = ChatServicer()

    pong = servicer.Ping(chat_pb2.PingRequest(), _FakeContext())
    assert pong.version, "expected a version"
    assert pong.HasField("server_time")

    try:
        servicer.SendMessage(chat_pb2.SendMessageRequest(room_id="r1"), _FakeContext())
    except _AbortError as exc:
        assert exc.code == grpc.StatusCode.UNIMPLEMENTED, exc.code
    else:
        raise AssertionError("expected SendMessage to abort UNIMPLEMENTED")

    print("OK: Ping returns version; SendMessage aborts UNIMPLEMENTED")


if __name__ == "__main__":
    run()
