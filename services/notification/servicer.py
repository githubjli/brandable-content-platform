"""Notification servicer — implements NotificationService RPCs."""

from __future__ import annotations

import logging

from google.protobuf import empty_pb2

logger = logging.getLogger(__name__)

# Import generated code. Proto compilation via `make proto-gen` puts the
# generated files into services/notification/generated/.
try:
    # generated/ is on sys.path (added by main.py), so the proto package resolves
    # as notification.v1.xxx matching the proto package declaration.
    from notification.v1 import notification_pb2, notification_pb2_grpc  # type: ignore[import]
except ImportError:
    # Allow importing without generated files (e.g. type-checking before proto-gen)
    notification_pb2 = None  # type: ignore[assignment]
    notification_pb2_grpc = None  # type: ignore[assignment]


class NotificationServicer:
    """Concrete implementation of NotificationService."""

    def Ping(self, request: empty_pb2.Empty, context: object) -> object:  # type: ignore[override]
        logger.info("Ping received")
        if notification_pb2 is None:
            raise RuntimeError("Generated proto code not available")
        return notification_pb2.PongResponse(message="pong")
