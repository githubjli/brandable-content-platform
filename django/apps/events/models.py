"""Models for events — the transactional Outbox (events.md §2, ADR-0003).

OutboxEvent rows are produced inside the business transaction (EventBus.emit) and
consumed asynchronously by the dispatcher. Unlike the wallet ledger / audit log,
the Outbox row is *not* immutable — its `status` advances through a lifecycle.
"""

from __future__ import annotations

from django.db.models import (
    CASCADE,
    CharField,
    DateTimeField,
    ForeignKey,
    Index,
    IntegerField,
    JSONField,
    PositiveSmallIntegerField,
    TextField,
    UniqueConstraint,
    UUIDField,
)
from django.utils import timezone

from libs.errors.base_model import AbstractBaseModel


class OutboxEvent(AbstractBaseModel):
    """A single domain event awaiting (or past) dispatch."""

    PENDING = "pending"
    DISPATCHED = "dispatched"
    PROCESSED = "processed"
    FAILED = "failed"
    DLQ = "dlq"
    STATUS = [
        (PENDING, PENDING),
        (DISPATCHED, DISPATCHED),
        (PROCESSED, PROCESSED),
        (FAILED, FAILED),
        (DLQ, DLQ),
    ]

    event_type = CharField(max_length=128)
    event_version = PositiveSmallIntegerField(default=1)
    idempotency_key = CharField(max_length=128)
    payload = JSONField(default=dict)
    headers = JSONField(default=dict)  # trace_id, request_id, actor_id, source_service, ...
    status = CharField(max_length=16, choices=STATUS, default=PENDING)
    retry_count = IntegerField(default=0)
    last_error = TextField(blank=True)
    dispatched_at = DateTimeField(null=True, blank=True)
    processed_at = DateTimeField(null=True, blank=True)
    available_at = DateTimeField(default=timezone.now)  # backoff scheduling

    class Meta:
        db_table = "outbox_event"
        ordering = ["created_at"]
        constraints = [
            UniqueConstraint(
                fields=["event_type", "idempotency_key"], name="outbox_idempotency_unique"
            ),
        ]
        indexes = [
            Index(fields=["status", "available_at"], name="idx_outbox_pending"),
            Index(fields=["event_type", "created_at"], name="idx_outbox_type_created"),
        ]

    def __str__(self) -> str:
        return f"OutboxEvent({self.event_type}, {self.status})"


class OutboxEventHandlerAck(AbstractBaseModel):
    """One row per (event, handler) that has successfully processed the event."""

    event = ForeignKey(OutboxEvent, on_delete=CASCADE, related_name="handler_acks")
    handler_name = CharField(max_length=255)

    class Meta:
        db_table = "outbox_event_handler_ack"
        constraints = [
            UniqueConstraint(fields=["event", "handler_name"], name="outbox_ack_unique"),
        ]

    def __str__(self) -> str:
        return f"Ack({self.event_id}, {self.handler_name})"


class OutboxEventDLQ(AbstractBaseModel):
    """Dead-letter: events that exhausted retries (retry_count >= 5). Needs a human."""

    original_event_id = UUIDField()
    event_type = CharField(max_length=128)
    payload = JSONField(default=dict)
    headers = JSONField(default=dict)
    failure_history = JSONField(default=list)  # [{timestamp, error, retry_count}]
    moved_at = DateTimeField(default=timezone.now)
    resolved_at = DateTimeField(null=True, blank=True)
    resolved_by = UUIDField(null=True, blank=True)  # admin user id
    resolution_note = TextField(blank=True)

    class Meta:
        db_table = "outbox_event_dlq"
        ordering = ["-moved_at"]

    def __str__(self) -> str:
        return f"DLQ({self.event_type}, original={self.original_event_id})"
