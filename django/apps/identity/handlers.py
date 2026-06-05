"""Identity event handlers (events.md §7). Registered at AppConfig.ready()."""

from __future__ import annotations

import logging
from typing import Any

from apps.events import types
from apps.events.registry import on_event

logger = logging.getLogger(__name__)


@on_event(types.IDENTITY_USER_REGISTERED)
def send_welcome_email(event: Any) -> None:
    """Send the welcome email via NotificationService.

    The Notification gRPC service lands in Week 11; until then this logs the
    intent so the end-to-end Outbox path (emit → dispatch → handler → ack) is
    exercised without a downstream dependency.
    """
    logger.info("identity.welcome_email_queued", extra={"user_id": event.payload.get("user_id")})
