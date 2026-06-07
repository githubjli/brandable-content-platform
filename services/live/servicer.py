"""LiveRuntime servicer — implements LiveRuntimeService RPCs (live-runtime.md §0, §1, §5).

Architecture-first scope (spec §0) is now implemented against the Ant Media
adapter (fake-mode until a real server is wired) plus an in-memory viewer-presence
registry: stream lifecycle (Create/Start/Stop/Delete), viewer playback config
(GetWatchConfig/GetStreamStatus). The service owns all Ant Media calls; Django
never touches Ant Media directly (ADR-0006).

Deferred to full V3 (still UNIMPLEMENTED): broadcast fan-out (chat/gift/
moderation) and the viewer-event feedback stream back into Django.

Stream status here is the runtime's *ephemeral* Ant Media view; Django remains
the source of truth for stream metadata and the persisted lifecycle state.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

import grpc
from google.protobuf.timestamp_pb2 import Timestamp

from ant_media import AntMediaAdapter
from presence import ViewerPresenceRegistry

logger = logging.getLogger(__name__)

SERVICE_VERSION = "0.2.0"

# Runtime-side ephemeral status values (mirror Django's lifecycle vocabulary).
IDLE = "idle"
READY = "ready"
LIVE = "live"
ENDED = "ended"
FAILED = "failed"

try:
    from live.v1 import live_runtime_pb2  # type: ignore[import]
except ImportError:
    live_runtime_pb2 = None  # type: ignore[assignment]


def _now_ts() -> Timestamp:
    ts = Timestamp()
    ts.GetCurrentTime()
    return ts


@dataclass
class _StreamState:
    stream_id: str
    owner_user_id: str = ""
    status: str = READY
    ant_media_stream_id: str = ""
    stream_key: str = ""
    rtmp_url: str = ""
    websocket_url: str = ""
    ice_servers_json: str = "[]"
    ant_media_session_id: str = ""
    created_ts: Timestamp = field(default_factory=_now_ts)


class LiveRuntimeServicer:
    """Concrete implementation of LiveRuntimeService.

    `ant_media` and `presence` are injectable so tests can run fully in-process.
    """

    def __init__(
        self,
        *,
        ant_media: AntMediaAdapter | None = None,
        presence: ViewerPresenceRegistry | None = None,
    ) -> None:
        self._ant = ant_media or AntMediaAdapter()
        self._presence = presence or ViewerPresenceRegistry()
        self._lock = threading.Lock()
        self._streams: dict[str, _StreamState] = {}

    # --- Health -----------------------------------------------------------

    def Ping(self, request: object, context: object) -> object:  # type: ignore[override]
        logger.info("Ping received")
        if live_runtime_pb2 is None:
            raise RuntimeError("Generated proto code not available")
        return live_runtime_pb2.PingResponse(version=SERVICE_VERSION, server_time=_now_ts())

    # --- Stream lifecycle -------------------------------------------------

    def CreateStream(self, request: Any, context: Any) -> object:  # type: ignore[override]
        if not request.stream_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "stream_id is required")
        with self._lock:
            state = self._streams.get(request.stream_id)
            if state is None:
                # Idempotent: re-creating an existing stream returns the same config.
                creds = self._ant.create_stream(
                    stream_id=request.stream_id, owner_user_id=request.owner_user_id
                )
                state = _StreamState(
                    stream_id=request.stream_id,
                    owner_user_id=request.owner_user_id,
                    status=READY,
                    ant_media_stream_id=creds["ant_media_stream_id"],
                    stream_key=creds["stream_key"],
                    rtmp_url=creds["rtmp_url"],
                    websocket_url=creds["websocket_url"],
                    ice_servers_json=creds.get("ice_servers_json", "[]"),
                )
                self._streams[request.stream_id] = state
        return live_runtime_pb2.StreamConfig(
            stream_id=state.stream_id,
            stream_key=state.stream_key,
            rtmp_url=state.rtmp_url,
            webrtc=live_runtime_pb2.WebRTCPublishConfig(
                websocket_url=state.websocket_url,
                ice_servers_json=state.ice_servers_json,
            ),
            created_at=state.created_ts,
        )

    def StartBroadcast(self, request: Any, context: Any) -> object:  # type: ignore[override]
        state = self._require(request.stream_id, context)
        with self._lock:
            already = state.status == LIVE
            if not already:
                if state.status not in {IDLE, READY}:
                    context.abort(
                        grpc.StatusCode.FAILED_PRECONDITION,
                        f"cannot start a stream in status {state.status}",
                    )
                result = self._ant.start_broadcast(stream_id=state.stream_id)
                state.status = LIVE
                state.ant_media_session_id = result.get("ant_media_session_id", "")
        return live_runtime_pb2.StartBroadcastResponse(
            stream_id=state.stream_id,
            status=state.status,
            already_started=already,
            ant_media_session_id=state.ant_media_session_id,
        )

    def StopBroadcast(self, request: Any, context: Any) -> object:  # type: ignore[override]
        state = self._require(request.stream_id, context)
        with self._lock:
            if state.status != ENDED:
                self._ant.stop_broadcast(stream_id=state.stream_id)
                state.status = ENDED
        self._presence.clear(stream_id=state.stream_id)
        return live_runtime_pb2.StopBroadcastResponse(
            stream_id=state.stream_id, status=state.status
        )

    def DeleteStream(self, request: Any, context: Any) -> object:  # type: ignore[override]
        with self._lock:
            state = self._streams.pop(request.stream_id, None)
        deleted = False
        if state is not None:
            deleted = self._ant.delete_stream(stream_id=request.stream_id)
            self._presence.clear(stream_id=request.stream_id)
        return live_runtime_pb2.DeleteStreamResponse(stream_id=request.stream_id, deleted=deleted)

    # --- Viewer playback config -------------------------------------------

    def GetWatchConfig(self, request: Any, context: Any) -> object:  # type: ignore[override]
        with self._lock:
            state = self._streams.get(request.stream_id)
            status = state.status if state else IDLE
        is_live = status == LIVE
        # Record real-time presence (dedup per viewer key); Django keeps its own
        # persisted unique-per-minute counter separately.
        if is_live:
            viewer_key = request.viewer_user_id or (
                f"anon:{request.viewer_ip}" if request.viewer_ip else "anon"
            )
            self._presence.join(stream_id=request.stream_id, viewer_key=viewer_key)
        cfg = self._ant.watch_config(stream_id=request.stream_id, is_live=is_live)
        return live_runtime_pb2.WatchConfig(
            live_id=request.stream_id,
            status=status,
            effective_status=status,
            viewer_count=self._presence.count(stream_id=request.stream_id),
            playback=self._playback(cfg["playback"]),
            fallback=self._playback(cfg["fallback"]),
        )

    def GetStreamStatus(self, request: Any, context: Any) -> object:  # type: ignore[override]
        state = self._require(request.stream_id, context)
        is_live = state.status == LIVE
        health = self._ant.broadcast_health(stream_id=state.stream_id, is_live=is_live)
        return live_runtime_pb2.StreamStatusResponse(
            stream_id=state.stream_id,
            status=state.status,
            effective_status=state.status,
            can_start=state.status in {IDLE, READY},
            can_end=is_live,
            viewer_count=self._presence.count(stream_id=state.stream_id),
            publish=live_runtime_pb2.PublishHealth(
                connected=health["publish"]["connected"], status=health["publish"]["status"]
            ),
            play=live_runtime_pb2.PlayHealth(
                connected=health["play"]["connected"], status=health["play"]["status"]
            ),
        )

    # --- Broadcasts (deferred to full V3) ---------------------------------

    def BroadcastChat(self, request: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "BroadcastChat")

    def BroadcastGift(self, request: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "BroadcastGift")

    def BroadcastModeration(self, request: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "BroadcastModeration")

    # --- Viewer events (deferred to full V3) ------------------------------

    def StreamViewerEvents(self, request: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "StreamViewerEvents")

    # --- Helpers ----------------------------------------------------------

    def _playback(self, p: dict[str, Any]) -> object:
        return live_runtime_pb2.Playback(
            mode=p["mode"],
            stream_id=p["stream_id"],
            websocket_url=p.get("websocket_url", ""),
            hls_url=p.get("hls_url", ""),
            connected=p.get("connected", False),
        )

    def _require(self, stream_id: str, context: Any) -> _StreamState:
        with self._lock:
            state = self._streams.get(stream_id)
        if state is None:
            context.abort(grpc.StatusCode.NOT_FOUND, f"stream {stream_id} is not known to runtime")
        return state  # type: ignore[return-value]

    def _unimplemented(self, context: Any, rpc: str) -> object:
        context.abort(
            grpc.StatusCode.UNIMPLEMENTED,
            f"{rpc} is deferred to the full V3 live-runtime build.",
        )
