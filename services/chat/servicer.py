"""Chat servicer — implements ChatService RPCs (chat.md §1-2).

V1 skeleton: Ping is implemented; the room/message RPCs are declared and return
UNIMPLEMENTED until the business logic + Postgres store land. The data model
lives in schema.sql; the service stores user IDs only and never reads Django's DB
(ADR-0006).
"""

from __future__ import annotations

import logging

import grpc
from google.protobuf.timestamp_pb2 import Timestamp

logger = logging.getLogger(__name__)

SERVICE_VERSION = "0.1.0"

# Import generated code (generated/ is on sys.path via main.py).
try:
    from chat.v1 import chat_pb2, chat_pb2_grpc  # type: ignore[import]
except ImportError:
    chat_pb2 = None  # type: ignore[assignment]
    chat_pb2_grpc = None  # type: ignore[assignment]


def _now_ts() -> Timestamp:
    ts = Timestamp()
    ts.GetCurrentTime()
    return ts


class ChatServicer:
    """Concrete implementation of ChatService."""

    def Ping(self, request: object, context: object) -> object:  # type: ignore[override]
        logger.info("Ping received")
        if chat_pb2 is None:
            raise RuntimeError("Generated proto code not available")
        return chat_pb2.PingResponse(version=SERVICE_VERSION, server_time=_now_ts())

    # --- Rooms -------------------------------------------------------------
    def CreateDirectRoom(self, request: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "CreateDirectRoom")

    def GetRoom(self, request: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "GetRoom")

    def ListUserRooms(self, request: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "ListUserRooms")

    # --- Messages ----------------------------------------------------------
    def SendMessage(self, request: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "SendMessage")

    def ListMessages(self, request: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "ListMessages")

    # --- Real-time stream --------------------------------------------------
    def Subscribe(self, request_iterator: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "Subscribe")

    # --- Mark read ---------------------------------------------------------
    def MarkRead(self, request: object, context: object) -> object:  # type: ignore[override]
        return self._unimplemented(context, "MarkRead")

    def _unimplemented(self, context: object, rpc: str) -> object:
        context.abort(  # type: ignore[attr-defined]
            grpc.StatusCode.UNIMPLEMENTED,
            f"{rpc} is not implemented in the V1 chat skeleton.",
        )
