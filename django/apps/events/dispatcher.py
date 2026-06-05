"""Outbox dispatcher (events.md §6).

V1 runs handlers **synchronously** inside the dispatch loop rather than fanning
out to Celery tasks: the transactional-outbox guarantees (at-least-once, retry
with backoff, DLQ at retry>=5, per-handler ack idempotency) are all preserved and
the engine is fully unit-testable without a broker. Celery fan-out is a later
optimisation. `dispatch_pending_batch()` is the testable core; `run_dispatcher`
(management command) wraps it in an advisory-locked poll loop.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from .models import OutboxEvent, OutboxEventDLQ, OutboxEventHandlerAck
from .registry import SkipHandler, get_handlers
from .services import EventBus

logger = logging.getLogger("events.dispatcher")

MAX_RETRIES = 5
# Exponential backoff with cap, indexed by retry_count (events.md §6).
BACKOFF_SECONDS = [5, 30, 120, 600, 1800]


def _backoff(retry_count: int) -> int:
    return BACKOFF_SECONDS[min(retry_count, len(BACKOFF_SECONDS) - 1)]


def _to_dlq(event: OutboxEvent, error: str) -> None:
    OutboxEventDLQ.objects.create(
        original_event_id=event.id,
        event_type=event.event_type,
        payload=event.payload,
        headers=event.headers,
        failure_history=[
            {
                "timestamp": timezone.now().isoformat(),
                "error": error,
                "retry_count": event.retry_count,
            }
        ],
    )
    event.status = OutboxEvent.DLQ
    event.last_error = error
    event.save(update_fields=["status", "last_error", "retry_count", "updated_at"])
    logger.critical("outbox.dlq", extra={"event_id": str(event.id), "event_type": event.event_type})


def _record_failure(event: OutboxEvent, error: str) -> None:
    event.retry_count += 1
    event.last_error = error
    if event.retry_count >= MAX_RETRIES:
        _to_dlq(event, error)
        return
    event.status = OutboxEvent.FAILED
    event.available_at = timezone.now() + timedelta(seconds=_backoff(event.retry_count))
    event.save(update_fields=["retry_count", "last_error", "status", "available_at", "updated_at"])


def _process_event(event: OutboxEvent) -> str:
    """Run all handlers for one event. Returns the resulting status string."""
    handlers = get_handlers(event.event_type, version=event.event_version)

    event.status = OutboxEvent.DISPATCHED
    event.dispatched_at = timezone.now()
    event.save(update_fields=["status", "dispatched_at", "updated_at"])

    acked = set(
        OutboxEventHandlerAck.objects.filter(event=event).values_list("handler_name", flat=True)
    )

    for handler in handlers:
        if handler.name in acked:
            continue  # already processed on a prior delivery — idempotent
        try:
            with transaction.atomic():
                handler.fn(event)
                EventBus.ack(str(event.id), handler.name)
        except SkipHandler:
            EventBus.ack(str(event.id), handler.name)
        except Exception as exc:
            logger.warning(
                "outbox.handler_failed",
                extra={"event_id": str(event.id), "handler": handler.name, "error": str(exc)},
            )
            _record_failure(event, f"{handler.name}: {exc}")
            return event.status

    event.status = OutboxEvent.PROCESSED
    event.processed_at = timezone.now()
    event.save(update_fields=["status", "processed_at", "updated_at"])
    return event.status


def dispatch_pending_batch(limit: int = 100) -> dict[str, int]:
    """Process one batch of due events. Returns counts by resulting status."""
    now = timezone.now()
    events = list(
        OutboxEvent.objects.filter(
            status__in=[OutboxEvent.PENDING, OutboxEvent.FAILED],
            available_at__lte=now,
        ).order_by("created_at")[:limit]
    )

    counts = {"processed": 0, "failed": 0, "dlq": 0}
    for event in events:
        status = _process_event(event)
        if status == OutboxEvent.PROCESSED:
            counts["processed"] += 1
        elif status == OutboxEvent.DLQ:
            counts["dlq"] += 1
        else:
            counts["failed"] += 1
    return counts


def replay_from_dlq(dlq_id: str) -> str:
    """Re-insert a DLQ entry as a fresh pending OutboxEvent (events.md §12)."""
    dlq = OutboxEventDLQ.objects.get(id=dlq_id)
    event = OutboxEvent.objects.create(
        event_type=dlq.event_type,
        idempotency_key=f"replay:{dlq.id}",
        payload=dlq.payload,
        headers=dlq.headers,
        status=OutboxEvent.PENDING,
    )
    dlq.resolved_at = timezone.now()
    dlq.resolution_note = f"replayed as {event.id}"
    dlq.save(update_fields=["resolved_at", "resolution_note"])
    return str(event.id)


def resolve_dlq(dlq_id: str, resolved_by: str, note: str) -> None:
    dlq = OutboxEventDLQ.objects.get(id=dlq_id)
    dlq.resolved_at = timezone.now()
    dlq.resolved_by = resolved_by
    dlq.resolution_note = note
    dlq.save(update_fields=["resolved_at", "resolved_by", "resolution_note"])


def _params() -> dict[str, Any]:  # pragma: no cover - convenience for the command
    return {"max_retries": MAX_RETRIES, "backoff": BACKOFF_SECONDS}
