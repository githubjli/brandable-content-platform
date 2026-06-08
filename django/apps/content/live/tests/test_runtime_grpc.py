"""Tests for the Live Runtime gRPC client + the Django runtime adapter wiring.

CI can't run the gRPC server, so the stub + channel are mocked: we verify the
client builds the right proto requests and maps responses to the dict shapes
apps/content/live/runtime.py expects, and that the adapter delegates to the
client only when LIVE_RUNTIME_ENABLED.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from apps.content.live import runtime


def _playback(**kw) -> SimpleNamespace:
    base = {
        "mode": "webrtc",
        "stream_id": "ams_x",
        "websocket_url": "wss://rt/ws",
        "hls_url": "https://rt/x.m3u8",
        "connected": True,
    }
    base.update(kw)
    return SimpleNamespace(**base)


class _CapturingStub:
    """Records the request passed to each RPC and returns a canned response."""

    last: dict = {}

    def __init__(self, channel):
        pass

    def CreateStream(self, request, metadata=None, timeout=None):  # noqa: N802 — gRPC name
        _CapturingStub.last = {"method": "CreateStream", "request": request, "timeout": timeout}
        return SimpleNamespace(
            stream_id=request.stream_id,
            stream_key="key_ams_x",
            rtmp_url="rtmp://rt/LiveApp/ams_x",
            webrtc=SimpleNamespace(websocket_url="wss://rt/ws", ice_servers_json="[]"),
        )

    def StartBroadcast(self, request, metadata=None, timeout=None):  # noqa: N802
        _CapturingStub.last = {"method": "StartBroadcast", "request": request}
        return SimpleNamespace(
            stream_id=request.stream_id,
            status="live",
            already_started=False,
            ant_media_session_id="sess_1",
        )

    def StopBroadcast(self, request, metadata=None, timeout=None):  # noqa: N802
        _CapturingStub.last = {"method": "StopBroadcast", "request": request}
        return SimpleNamespace(stream_id=request.stream_id, status="ended")

    def GetWatchConfig(self, request, metadata=None, timeout=None):  # noqa: N802
        _CapturingStub.last = {"method": "GetWatchConfig", "request": request}
        return SimpleNamespace(playback=_playback(), fallback=_playback(mode="hls"))


_FAKE_PB2 = SimpleNamespace(
    CreateStreamRequest=lambda **kw: SimpleNamespace(**kw),
    StartBroadcastRequest=lambda **kw: SimpleNamespace(**kw),
    StopBroadcastRequest=lambda **kw: SimpleNamespace(**kw),
    GetWatchConfigRequest=lambda **kw: SimpleNamespace(**kw),
)
_FAKE_GRPC = SimpleNamespace(LiveRuntimeServiceStub=_CapturingStub)


def _patch_client(monkeypatch):
    from libs import grpc_client

    monkeypatch.setattr(grpc_client, "_live_runtime_modules", lambda: (_FAKE_PB2, _FAKE_GRPC))
    monkeypatch.setattr(grpc_client.grpc, "insecure_channel", lambda addr: MagicMock())
    return grpc_client


class TestLiveRuntimeClient:
    def test_create_stream_builds_request_and_maps_config(self, monkeypatch):
        gc = _patch_client(monkeypatch)
        result = gc.live_create_stream(stream_id="s1", owner_id="u1", title="T")
        req = _CapturingStub.last["request"]
        assert _CapturingStub.last["method"] == "CreateStream"
        assert req.stream_id == "s1"
        assert req.owner_user_id == "u1"
        assert req.idempotency_key == "live_create:s1"
        assert result["stream_key"] == "key_ams_x"
        assert result["rtmp_url"].startswith("rtmp://")
        assert result["websocket_url"] == "wss://rt/ws"
        assert result["webrtc_publish_config"]["ice_servers_json"] == "[]"

    def test_start_broadcast_maps_response(self, monkeypatch):
        gc = _patch_client(monkeypatch)
        result = gc.live_start_broadcast(stream_id="s1")
        assert result == {
            "ok": True,
            "status": "live",
            "already_started": False,
            "ant_media_session_id": "sess_1",
        }
        assert _CapturingStub.last["request"].idempotency_key == "live_start:s1"

    def test_stop_broadcast_maps_response(self, monkeypatch):
        gc = _patch_client(monkeypatch)
        assert gc.live_stop_broadcast(stream_id="s1") == {"ok": True, "status": "ended"}

    def test_watch_config_maps_playback_and_fallback(self, monkeypatch):
        gc = _patch_client(monkeypatch)
        result = gc.live_get_watch_config(stream_id="s1", viewer_user_id="v1")
        assert result["playback"]["mode"] == "webrtc"
        assert result["playback"]["connected"] is True
        assert result["fallback"]["mode"] == "hls"
        assert _CapturingStub.last["request"].viewer_user_id == "v1"


class TestRuntimeAdapterWiring:
    def test_fake_mode_does_not_call_client(self, settings):
        settings.LIVE_RUNTIME_ENABLED = False
        with patch("libs.grpc_client.live_create_stream") as call:
            out = runtime.create_stream(stream_id="s1", owner_id="u1")
        call.assert_not_called()
        assert out["ant_media_stream_id"].startswith("ams_")

    def test_enabled_create_delegates_to_client(self, settings):
        settings.LIVE_RUNTIME_ENABLED = True
        with patch("libs.grpc_client.live_create_stream") as call:
            call.return_value = {"stream_key": "k"}
            out = runtime.create_stream(stream_id="s1", owner_id="u1")
        call.assert_called_once_with(stream_id="s1", owner_id="u1")
        assert out == {"stream_key": "k"}

    def test_enabled_start_stop_delegate(self, settings):
        settings.LIVE_RUNTIME_ENABLED = True
        with (
            patch("libs.grpc_client.live_start_broadcast") as start,
            patch("libs.grpc_client.live_stop_broadcast") as stop,
        ):
            start.return_value = {"ok": True, "status": "live"}
            stop.return_value = {"ok": True, "status": "ended"}
            runtime.start_broadcast(stream_id="s1")
            runtime.stop_broadcast(stream_id="s1")
        start.assert_called_once_with(stream_id="s1")
        stop.assert_called_once_with(stream_id="s1")

    def test_enabled_watch_config_delegates(self, settings):
        settings.LIVE_RUNTIME_ENABLED = True
        with patch("libs.grpc_client.live_get_watch_config") as call:
            call.return_value = {"playback": {}, "fallback": {}}
            runtime.get_watch_config(stream_id="s1", ant_media_stream_id="", is_live=True)
        call.assert_called_once_with(stream_id="s1")
