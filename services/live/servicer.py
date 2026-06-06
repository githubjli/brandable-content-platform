"""LiveRuntime servicer — implements LiveRuntimeService RPCs (live-runtime.md §1, §5).

V1 skeleton: Ping is implemented; stream-lifecycle, watch-config, and broadcast
RPCs are declared and return UNIMPLEMENTED until the Ant Media integration +
Redis-backed viewer presence land. The service owns all Ant Media REST calls;
Django never imports Ant Media directly (ADR-0006).
"""

from __future__ import annotations

import logging

import grpc
from google.protobuf.timestamp_pb2 import Timestamp

logger = logging.getLogger(__name__)

SERVICE_VERSION = "0.1.0"

try:
    from live.v1 import live_runtime_pb2, live_runtime_pb2_grpc  # type: ignore[import]
except ImportError:
    live_runtime_pb2 = None  # type: ignore[assignment]
    live_runtime_pb2_grpc = None  # type: ignore[assignment]


def _now_ts() -> Timestamp:
    ts = Timestamp()
    ts.GetCurrentTime()
    return ts


class LiveRuntimeServicer:
    """Concrete implementation of LiveRuntimeService."""

    def Ping(self, request: object, context: object) -> object:  # type: ignore[override]
        logger.info("Ping received")
        if live_runtime_pb2 is None:
            raise RuntimeError("Generated proto code not available")
        return live_runtime_pb2.PingResponse(version=SERVICE_VERSION, server_time=_now_ts())

    # --- Stream lifecycle -------------------------------------------------
    def CreateStream(self, request: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "CreateStream")

    def StartBroadcast(self, request: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "StartBroadcast")

    def StopBroadcast(self, request: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "StopBroadcast")

    def DeleteStream(self, request: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "DeleteStream")

    # --- Viewer playback config -------------------------------------------
    def GetWatchConfig(self, request: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "GetWatchConfig")

    def GetStreamStatus(self, request: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "GetStreamStatus")

    # --- Broadcasts -------------------------------------------------------
    def BroadcastChat(self, request: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "BroadcastChat")

    def BroadcastGift(self, request: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "BroadcastGift")

    def BroadcastModeration(self, request: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "BroadcastModeration")

    # --- Viewer events ----------------------------------------------------
    def StreamViewerEvents(self, request: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "StreamViewerEvents")

    def _unimplemented(self, context: object, rpc: str) -> object:
        context.abort(  # type: ignore[attr-defined]
            grpc.StatusCode.UNIMPLEMENTED,
            f"{rpc} is not implemented in the V1 live-runtime skeleton.",
        )
