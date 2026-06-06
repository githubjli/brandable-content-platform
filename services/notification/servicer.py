"""Notification servicer — implements NotificationService RPCs."""

from __future__ import annotations

import logging
import uuid

from google.protobuf import empty_pb2

logger = logging.getLogger(__name__)

# Idempotency cache for Send (in-process; a durable store lands with the real
# email backend). Maps idempotency_key -> message_id.
_SENT: dict[str, str] = {}

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

    def Send(self, request: object, context: object) -> object:  # type: ignore[override]
        """Dispatch a templated notification.

        V1 canary: the real email backend isn't wired yet, so this validates the
        request, logs the intent, and returns QUEUED. Idempotent on
        idempotency_key (a repeat returns the same message_id, status DUPLICATE).
        """
        if notification_pb2 is None:
            raise RuntimeError("Generated proto code not available")

        key = request.idempotency_key  # type: ignore[attr-defined]
        if not key:
            return notification_pb2.SendResponse(status="FAILED", message_id="")
        if key in _SENT:
            return notification_pb2.SendResponse(status="DUPLICATE", message_id=_SENT[key])

        message_id = uuid.uuid4().hex
        _SENT[key] = message_id
        logger.info(
            "Send queued",
            extra={
                "channel": request.channel,  # type: ignore[attr-defined]
                "template_code": request.template_code,  # type: ignore[attr-defined]
                "recipient_user_id": request.recipient_user_id,  # type: ignore[attr-defined]
                "message_id": message_id,
            },
        )
        return notification_pb2.SendResponse(status="QUEUED", message_id=message_id)
