"""Live Runtime adapter (content-live.md architecture split).

The realtime plane (Ant Media bridge, WebSocket gateway, broadcast) lives in the
`services/live_runtime` gRPC service. This module is the Django-side client.

Until the gRPC client is wired (settings.LIVE_RUNTIME_ENABLED), it runs in
**fake mode** and returns synthetic credentials/config so the full stream
lifecycle works end-to-end in dev/test without a running runtime — the same
pattern the Stripe adapter uses.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return bool(getattr(settings, "LIVE_RUNTIME_ENABLED", False))


def create_stream(*, stream_id: str, owner_id: str) -> dict[str, Any]:
    """Provision realtime credentials for a new stream."""
    if not _enabled():
        sid = f"ams_{str(stream_id).replace('-', '')[:20]}"
        return {
            "ant_media_stream_id": sid,
            "stream_key": f"key_{sid}",
            "rtmp_url": f"rtmp://live-runtime.local/LiveApp/{sid}",
            "hls_url": f"https://live-runtime.local/LiveApp/streams/{sid}.m3u8",
            "websocket_url": "wss://live-runtime.local/LiveApp/websocket",
            "webrtc_publish_config": {"mode": "webrtc", "stream_id": sid},
        }
    raise NotImplementedError("Live Runtime gRPC client not yet wired.")  # pragma: no cover


def start_broadcast(*, stream_id: str) -> dict[str, Any]:
    if not _enabled():
        return {"ok": True}
    raise NotImplementedError("Live Runtime gRPC client not yet wired.")  # pragma: no cover


def stop_broadcast(*, stream_id: str) -> dict[str, Any]:
    if not _enabled():
        return {"ok": True}
    raise NotImplementedError("Live Runtime gRPC client not yet wired.")  # pragma: no cover


def get_watch_config(*, stream_id: str, ant_media_stream_id: str, is_live: bool) -> dict[str, Any]:
    """Playback config for viewers (WebRTC primary, HLS fallback)."""
    if not _enabled():
        sid = ant_media_stream_id or f"ams_{str(stream_id).replace('-', '')[:20]}"
        hls = f"https://live-runtime.local/LiveApp/streams/{sid}.m3u8"
        return {
            "playback": {
                "mode": "webrtc",
                "stream_id": sid,
                "websocket_url": "wss://live-runtime.local/LiveApp/websocket",
                "hls_url": hls,
                "connected": is_live,
            },
            "fallback": {"mode": "hls", "hls_url": hls},
        }
    raise NotImplementedError("Live Runtime gRPC client not yet wired.")  # pragma: no cover
