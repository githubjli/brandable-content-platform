"""Identity event handlers (events.md §7). Registered at AppConfig.ready()."""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

from apps.events import types
from apps.events.registry import on_event

logger = logging.getLogger(__name__)


@on_event(types.IDENTITY_USER_REGISTERED)
def send_welcome_email(event: Any) -> None:
    """Send the welcome email via NotificationService.Send (Week 11 email canary).

    Gated by NOTIFICATION_ENABLED so dev/test (where the Notification gRPC service
    isn't running) no-op cleanly. When enabled, a gRPC failure propagates so the
    dispatcher retries/DLQs (events.md §7 handler semantics).
    """
    payload = event.payload
    if not getattr(settings, "NOTIFICATION_ENABLED", False):
        logger.info("notification disabled; skipping welcome email for %s", payload.get("user_id"))
        return

    from libs.grpc_client import send_notification

    result = send_notification(
        idempotency_key=f"welcome:{event.id}",
        channel="email",
        template_code="welcome",
        recipient_user_id=str(payload.get("user_id", "")),
        recipient_address=str(payload.get("email", "")),
        variables={"display_name": str(payload.get("display_name", ""))},
    )
    logger.info("welcome email %s for %s", result.get("status"), payload.get("user_id"))
