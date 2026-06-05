"""Service layer for events — the producer side of the Outbox (events.md §5).

EventBus.emit is the only correct way to produce an event. It must be called
inside the caller's business transaction so the event commits (or rolls back)
atomically with the business write.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from django.db import IntegrityError, transaction

from libs.errors.exceptions import AppError, ValidationError

from .models import OutboxEvent, OutboxEventHandlerAck

# <domain>.<PastTense> or <domain>.<sub>.<PastTense>; last segment PascalCase.
_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)*\.[A-Z][A-Za-z0-9]*$")
_MAX_KEY_LEN = 128


class EventAlreadyEmitted(AppError):  # noqa: N818 — contract-named exception (events.md §9)
    """Raised when (event_type, idempotency_key) already exists (events.md §9)."""

    http_status = 409
    default_code = "EVENT_ALREADY_EMITTED"
    default_message = "An event with this idempotency key already exists."


def _build_headers(actor_id: str | None, extra: dict[str, Any] | None) -> dict[str, Any]:
    try:
        from libs.telemetry import get_trace_id

        trace_id = get_trace_id() or ""
    except Exception:
        trace_id = ""
    headers = {
        "trace_id": trace_id,
        "request_id": str(uuid.uuid4()),
        "actor_id": str(actor_id) if actor_id else None,
        "source_service": "django",
        "occurred_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "brand_id": None,
    }
    if extra:
        headers.update(extra)
    return headers


class EventBus:
    @staticmethod
    def emit(
        *,
        event_type: str,
        idempotency_key: str,
        payload: dict[str, Any],
        actor_id: str | None = None,
        event_version: int = 1,
        headers: dict[str, Any] | None = None,
    ) -> str:
        """Insert a pending OutboxEvent. Returns its id.

        Raises ValidationError on a malformed event_type / key, and
        EventAlreadyEmitted if the (event_type, idempotency_key) pair exists.
        """
        if not _EVENT_TYPE_RE.match(event_type):
            raise ValidationError(
                code="EVENT_INVALID_TYPE",
                message=f"event_type '{event_type}' must be <domain>.<PastTense>.",
            )
        if not idempotency_key or len(idempotency_key) > _MAX_KEY_LEN:
            raise ValidationError(
                code="EVENT_INVALID_IDEMPOTENCY_KEY",
                message=f"idempotency_key is required and must be <= {_MAX_KEY_LEN} chars.",
            )

        try:
            # Savepoint so a dedup collision doesn't poison the caller's transaction.
            with transaction.atomic():
                event = OutboxEvent.objects.create(
                    event_type=event_type,
                    event_version=event_version,
                    idempotency_key=idempotency_key,
                    payload=payload,
                    headers=_build_headers(actor_id, headers),
                    status=OutboxEvent.PENDING,
                )
        except IntegrityError as exc:
            raise EventAlreadyEmitted(
                message=f"Event {event_type}/{idempotency_key} already emitted."
            ) from exc

        return str(event.id)

    @staticmethod
    def ack(event_id: str, handler_name: str) -> None:
        """Record that a handler has processed an event (idempotent)."""
        OutboxEventHandlerAck.objects.get_or_create(  # type: ignore[misc]
            event_id=event_id, handler_name=handler_name
        )


def emit(
    *,
    event_type: str,
    idempotency_key: str,
    payload: dict[str, Any],
    actor_id: str | None = None,
    event_version: int = 1,
) -> str:
    """Module-level alias for EventBus.emit (the form cross-app wrappers import)."""
    return EventBus.emit(
        event_type=event_type,
        idempotency_key=idempotency_key,
        payload=payload,
        actor_id=actor_id,
        event_version=event_version,
    )


# ---------------------------------------------------------------------------
# Admin read helpers (events.md §12)
# ---------------------------------------------------------------------------


def _iso(dt: Any) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def serialize_event(event: OutboxEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "event_type": event.event_type,
        "event_version": event.event_version,
        "idempotency_key": event.idempotency_key,
        "status": event.status,
        "retry_count": event.retry_count,
        "last_error": event.last_error or None,
        "payload": event.payload,
        "headers": event.headers,
        "created_at": _iso(event.created_at),
        "dispatched_at": _iso(event.dispatched_at),
        "processed_at": _iso(event.processed_at),
        "available_at": _iso(event.available_at),
    }


def outbox_queryset(
    *,
    event_type: str | None = None,
    status: str | None = None,
    actor_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    qs = OutboxEvent.objects.all()
    if event_type:
        qs = qs.filter(event_type=event_type)
    if status:
        qs = qs.filter(status=status)
    if actor_id:
        qs = qs.filter(headers__actor_id=actor_id)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    return qs


def get_outbox_detail(event_id: str) -> dict[str, Any]:
    from libs.errors.exceptions import NotFoundError

    try:
        event = OutboxEvent.objects.prefetch_related("handler_acks").get(id=event_id)
    except OutboxEvent.DoesNotExist:
        raise NotFoundError(code="EVENT_NOT_FOUND", message="Outbox event not found.")
    data = serialize_event(event)
    data["handler_acks"] = [
        {"handler_name": a.handler_name, "processed_at": _iso(a.created_at)}
        for a in event.handler_acks.all()
    ]
    return data
