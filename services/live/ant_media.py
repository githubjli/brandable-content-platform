"""Ant Media adapter for the Live Runtime service (live-runtime.md §0).

The runtime service is the *only* component that talks to Ant Media (ADR-0006);
Django reaches it through the gRPC RPCs, never directly. Until a real Ant Media
Server is wired (env ANT_MEDIA_ENABLED=true), this runs in **fake mode** and
returns synthetic credentials/config so the whole gRPC surface works end-to-end
in dev/test — the same pattern Django's apps/content/live/runtime.py uses.

The synthetic shapes mirror Django's fake-mode so a future real cutover is a
drop-in: ant_media_stream_id = "ams_<stream-uuid-no-dashes>"[:24], wss/rtmp/m3u8
URLs under the configured base host.
"""

from __future__ import annotations

import os
from typing import Any


def _enabled() -> bool:
    return os.environ.get("ANT_MEDIA_ENABLED", "").lower() in {"1", "true", "yes"}


def _base_host() -> str:
    return os.environ.get("ANT_MEDIA_BASE_URL", "https://live-runtime.local").rstrip("/")


def _app() -> str:
    return os.environ.get("ANT_MEDIA_APP", "LiveApp")


def _ws_host() -> str:
    host = _base_host()
    if host.startswith("https://"):
        return "wss://" + host[len("https://") :]
    if host.startswith("http://"):
        return "ws://" + host[len("http://") :]
    return "wss://" + host


def stream_key_for(stream_id: str) -> str:
    """Deterministic Ant Media stream id derived from the Django stream UUID."""
    return f"ams_{str(stream_id).replace('-', '')[:20]}"


class AntMediaError(RuntimeError):
    """Raised when an Ant Media REST call fails (real mode only)."""


class AntMediaAdapter:
    """Stateless wrapper over the Ant Media REST API (fake-mode until wired)."""

    def __init__(self, *, enabled: bool | None = None) -> None:
        self._enabled = _enabled() if enabled is None else enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    # --- Stream lifecycle -------------------------------------------------

    def create_stream(self, *, stream_id: str, owner_user_id: str) -> dict[str, Any]:
        """Provision a publishable broadcast and return publisher credentials."""
        if not self._enabled:
            sid = stream_key_for(stream_id)
            return {
                "ant_media_stream_id": sid,
                "stream_key": f"key_{sid}",
                "rtmp_url": f"rtmp://{_base_host().split('://')[-1]}/{_app()}/{sid}",
                "websocket_url": f"{_ws_host()}/{_app()}/websocket",
                "ice_servers_json": "[]",
            }
        raise NotImplementedError("Ant Media REST client not yet wired.")

    def start_broadcast(self, *, stream_id: str) -> dict[str, Any]:
        if not self._enabled:
            return {"ant_media_session_id": f"sess_{stream_key_for(stream_id)}", "status": "live"}
        raise NotImplementedError("Ant Media REST client not yet wired.")

    def stop_broadcast(self, *, stream_id: str) -> dict[str, Any]:
        if not self._enabled:
            return {"status": "ended"}
        raise NotImplementedError("Ant Media REST client not yet wired.")

    def delete_stream(self, *, stream_id: str) -> bool:
        if not self._enabled:
            return True
        raise NotImplementedError("Ant Media REST client not yet wired.")

    # --- Playback ---------------------------------------------------------

    def watch_config(self, *, stream_id: str, is_live: bool) -> dict[str, Any]:
        """Playback endpoints for viewers (WebRTC primary, HLS fallback)."""
        if not self._enabled:
            sid = stream_key_for(stream_id)
            hls = f"{_base_host()}/{_app()}/streams/{sid}.m3u8"
            return {
                "playback": {
                    "mode": "webrtc",
                    "stream_id": sid,
                    "websocket_url": f"{_ws_host()}/{_app()}/websocket",
                    "hls_url": hls,
                    "connected": is_live,
                },
                "fallback": {
                    "mode": "hls",
                    "stream_id": sid,
                    "websocket_url": "",
                    "hls_url": hls,
                    "connected": is_live,
                },
            }
        raise NotImplementedError("Ant Media REST client not yet wired.")

    def broadcast_health(self, *, stream_id: str, is_live: bool) -> dict[str, Any]:
        """Publish/play health for GetStreamStatus."""
        if not self._enabled:
            status = "live" if is_live else "idle"
            return {
                "publish": {"connected": is_live, "status": status},
                "play": {"connected": is_live, "status": status},
            }
        raise NotImplementedError("Ant Media REST client not yet wired.")
