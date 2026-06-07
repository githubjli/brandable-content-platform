#!/usr/bin/env python
"""Unit tests for LiveRuntimeServicer (no running server needed).

Covers the architecture-first scope (live-runtime.md §0): stream lifecycle,
watch-config + status against the fake-mode Ant Media adapter and in-memory
presence registry; deferred broadcast RPCs still abort UNIMPLEMENTED.

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

from ant_media import AntMediaAdapter  # noqa: E402
from presence import ViewerPresenceRegistry  # noqa: E402
from servicer import LiveRuntimeServicer  # noqa: E402

pb = live_runtime_pb2


class _AbortError(Exception):
    def __init__(self, code, details):
        self.code = code
        self.details = details


class _FakeContext:
    def abort(self, code, details):
        raise _AbortError(code, details)


_CTX = _FakeContext()


def _servicer() -> LiveRuntimeServicer:
    return LiveRuntimeServicer(
        ant_media=AntMediaAdapter(enabled=False), presence=ViewerPresenceRegistry()
    )


def _create(s: LiveRuntimeServicer, stream_id: str = "s1") -> object:
    return s.CreateStream(
        pb.CreateStreamRequest(stream_id=stream_id, owner_user_id="u1", title="T"), _CTX
    )


def test_ping():
    pong = _servicer().Ping(pb.PingRequest(), _CTX)
    assert pong.version, "expected a version"
    assert pong.HasField("server_time")


def test_create_stream_config_and_idempotent():
    s = _servicer()
    cfg = _create(s)
    assert cfg.stream_key, "expected a publisher stream_key"
    assert cfg.rtmp_url.startswith("rtmp://")
    assert cfg.webrtc.websocket_url.startswith("wss://")
    # Idempotent: same id → same key, no new credentials.
    again = _create(s)
    assert again.stream_key == cfg.stream_key


def test_create_requires_stream_id():
    try:
        _servicer().CreateStream(pb.CreateStreamRequest(stream_id=""), _CTX)
    except _AbortError as exc:
        assert exc.code == grpc.StatusCode.INVALID_ARGUMENT, exc.code
    else:
        raise AssertionError("expected INVALID_ARGUMENT")


def test_start_unknown_stream_not_found():
    try:
        _servicer().StartBroadcast(pb.StartBroadcastRequest(stream_id="nope"), _CTX)
    except _AbortError as exc:
        assert exc.code == grpc.StatusCode.NOT_FOUND, exc.code
    else:
        raise AssertionError("expected NOT_FOUND")


def test_full_lifecycle():
    s = _servicer()
    _create(s)

    started = s.StartBroadcast(pb.StartBroadcastRequest(stream_id="s1"), _CTX)
    assert started.status == "live"
    assert started.already_started is False
    assert started.ant_media_session_id

    # Idempotent re-start.
    restarted = s.StartBroadcast(pb.StartBroadcastRequest(stream_id="s1"), _CTX)
    assert restarted.already_started is True

    status = s.GetStreamStatus(pb.GetStreamStatusRequest(stream_id="s1"), _CTX)
    assert status.status == "live"
    assert status.can_end is True
    assert status.can_start is False
    assert status.publish.connected is True

    stopped = s.StopBroadcast(pb.StopBroadcastRequest(stream_id="s1"), _CTX)
    assert stopped.status == "ended"

    ended = s.GetStreamStatus(pb.GetStreamStatusRequest(stream_id="s1"), _CTX)
    assert ended.can_start is False and ended.can_end is False


def test_watch_config_presence_dedup():
    s = _servicer()
    _create(s)
    s.StartBroadcast(pb.StartBroadcastRequest(stream_id="s1"), _CTX)

    cfg = s.GetWatchConfig(pb.GetWatchConfigRequest(stream_id="s1", viewer_user_id="v1"), _CTX)
    assert cfg.playback.mode == "webrtc"
    assert cfg.playback.connected is True
    assert cfg.fallback.mode == "hls"
    assert cfg.viewer_count == 1

    # Same viewer → still 1; new viewer → 2.
    s.GetWatchConfig(pb.GetWatchConfigRequest(stream_id="s1", viewer_user_id="v1"), _CTX)
    assert s._presence.count(stream_id="s1") == 1
    s.GetWatchConfig(pb.GetWatchConfigRequest(stream_id="s1", viewer_user_id="v2"), _CTX)
    assert s._presence.count(stream_id="s1") == 2


def test_watch_config_not_live_no_presence():
    s = _servicer()
    _create(s)  # status READY, not live
    cfg = s.GetWatchConfig(pb.GetWatchConfigRequest(stream_id="s1", viewer_user_id="v1"), _CTX)
    assert cfg.playback.connected is False
    assert cfg.viewer_count == 0


def test_delete_stream():
    s = _servicer()
    _create(s)
    resp = s.DeleteStream(pb.DeleteStreamRequest(stream_id="s1"), _CTX)
    assert resp.deleted is True
    try:
        s.GetStreamStatus(pb.GetStreamStatusRequest(stream_id="s1"), _CTX)
    except _AbortError as exc:
        assert exc.code == grpc.StatusCode.NOT_FOUND
    else:
        raise AssertionError("expected NOT_FOUND after delete")
    # Deleting an unknown stream is a no-op (deleted=False), not an error.
    assert s.DeleteStream(pb.DeleteStreamRequest(stream_id="ghost"), _CTX).deleted is False


def test_broadcasts_still_unimplemented():
    s = _servicer()
    for call, req in (
        (s.BroadcastChat, pb.BroadcastChatRequest(stream_id="s1")),
        (s.BroadcastGift, pb.BroadcastGiftRequest(stream_id="s1")),
        (s.BroadcastModeration, pb.BroadcastModerationRequest(stream_id="s1")),
        (s.StreamViewerEvents, pb.StreamViewerEventsRequest()),
    ):
        try:
            call(req, _CTX)
        except _AbortError as exc:
            assert exc.code == grpc.StatusCode.UNIMPLEMENTED, exc.code
        else:
            raise AssertionError("expected UNIMPLEMENTED")


def test_presence_join_leave():
    reg = ViewerPresenceRegistry()
    assert reg.join(stream_id="x", viewer_key="a") == 1
    assert reg.join(stream_id="x", viewer_key="a") == 1  # dedup
    assert reg.join(stream_id="x", viewer_key="b") == 2
    remaining, duration = reg.leave(stream_id="x", viewer_key="a")
    assert remaining == 1
    assert duration >= 0
    reg.clear(stream_id="x")
    assert reg.count(stream_id="x") == 0


def test_ant_media_real_mode_not_implemented():
    adapter = AntMediaAdapter(enabled=True)
    try:
        adapter.create_stream(stream_id="s1", owner_user_id="u1")
    except NotImplementedError:
        pass
    else:
        raise AssertionError("expected NotImplementedError in real mode")


def run() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"OK: {len(tests)} live-runtime servicer tests passed")


if __name__ == "__main__":
    run()
