#!/usr/bin/env python
"""Unit test for LiveRuntimeServicer (no running server needed).

Run with:
    python services/live/test_servicer.py
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
    from live.v1 import live_runtime_pb2  # type: ignore[import]
except ImportError as exc:
    print(f"ERROR: Generated proto stubs not found: {exc}. Run `make proto-gen`.")
    sys.exit(1)

from servicer import LiveRuntimeServicer  # noqa: E402


class _AbortError(Exception):
    def __init__(self, code, details):
        self.code = code
        self.details = details


class _FakeContext:
    def abort(self, code, details):
        raise _AbortError(code, details)


def run() -> None:
    servicer = LiveRuntimeServicer()

    pong = servicer.Ping(live_runtime_pb2.PingRequest(), _FakeContext())
    assert pong.version, "expected a version"
    assert pong.HasField("server_time")

    try:
        servicer.CreateStream(live_runtime_pb2.CreateStreamRequest(stream_id="s1"), _FakeContext())
    except _AbortError as exc:
        assert exc.code == grpc.StatusCode.UNIMPLEMENTED, exc.code
    else:
        raise AssertionError("expected CreateStream to abort UNIMPLEMENTED")

    print("OK: Ping returns version; CreateStream aborts UNIMPLEMENTED")


if __name__ == "__main__":
    run()
