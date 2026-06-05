"""Tests for the Outbox: EventBus.emit, the dispatcher lifecycle, and DLQ."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.economy import services as economy_services
from apps.events import registry
from apps.events.dispatcher import dispatch_pending_batch, replay_from_dlq, resolve_dlq
from apps.events.models import OutboxEvent, OutboxEventDLQ, OutboxEventHandlerAck
from apps.events.registry import on_event
from apps.events.services import EventAlreadyEmitted, EventBus
from libs.errors.exceptions import ValidationError


@pytest.fixture
def temp_registry():
    """Snapshot + restore the global handler registry around a test."""
    snapshot = {k: list(v) for k, v in registry._REGISTRY.items()}
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(snapshot)


def _reset_available(event_id: str) -> None:
    OutboxEvent.objects.filter(id=event_id).update(available_at=timezone.now())


# ---------------------------------------------------------------------------
# EventBus.emit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEmit:
    def test_emit_inserts_pending_with_headers(self):
        eid = EventBus.emit(
            event_type="identity.UserRegistered",
            idempotency_key="user_registered:1",
            payload={"user_id": "1"},
            actor_id="1",
        )
        event = OutboxEvent.objects.get(id=eid)
        assert event.status == OutboxEvent.PENDING
        assert event.headers["source_service"] == "django"
        assert event.headers["actor_id"] == "1"
        assert "occurred_at" in event.headers

    def test_invalid_event_type_rejected(self):
        with pytest.raises(ValidationError) as exc:
            EventBus.emit(event_type="bad_name", idempotency_key="k", payload={})
        assert exc.value.code == "EVENT_INVALID_TYPE"

    def test_blank_idempotency_key_rejected(self):
        with pytest.raises(ValidationError) as exc:
            EventBus.emit(event_type="identity.UserRegistered", idempotency_key="", payload={})
        assert exc.value.code == "EVENT_INVALID_IDEMPOTENCY_KEY"

    def test_duplicate_key_raises(self):
        EventBus.emit(event_type="identity.UserRegistered", idempotency_key="dup", payload={})
        with pytest.raises(EventAlreadyEmitted):
            EventBus.emit(event_type="identity.UserRegistered", idempotency_key="dup", payload={})


# ---------------------------------------------------------------------------
# Dispatcher lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDispatch:
    def test_handler_runs_and_event_processed(self, temp_registry):
        calls = []

        @on_event("test.Thing")
        def handler(event):
            calls.append(event.id)

        eid = EventBus.emit(event_type="test.Thing", idempotency_key="k1", payload={"a": 1})
        counts = dispatch_pending_batch()

        assert counts["processed"] == 1
        assert len(calls) == 1
        event = OutboxEvent.objects.get(id=eid)
        assert event.status == OutboxEvent.PROCESSED
        assert event.processed_at is not None
        assert OutboxEventHandlerAck.objects.filter(event=event).count() == 1

    def test_event_with_no_handlers_is_processed(self):
        eid = EventBus.emit(event_type="test.Unhandled", idempotency_key="k1", payload={})
        dispatch_pending_batch()
        assert OutboxEvent.objects.get(id=eid).status == OutboxEvent.PROCESSED

    def test_already_acked_handler_is_skipped_on_redelivery(self, temp_registry):
        calls = []

        @on_event("test.Thing")
        def handler(event):
            calls.append(1)

        eid = EventBus.emit(event_type="test.Thing", idempotency_key="k1", payload={})
        dispatch_pending_batch()
        # Force re-delivery: the ack already exists, so the handler must not re-run.
        OutboxEvent.objects.filter(id=eid).update(
            status=OutboxEvent.FAILED, available_at=timezone.now()
        )
        dispatch_pending_batch()

        assert len(calls) == 1
        assert OutboxEvent.objects.get(id=eid).status == OutboxEvent.PROCESSED

    def test_failure_retries_with_backoff_then_dlq(self, temp_registry):
        @on_event("test.Boom")
        def boom(event):
            raise RuntimeError("handler exploded")

        eid = EventBus.emit(event_type="test.Boom", idempotency_key="k1", payload={})

        # First failure → FAILED, retry scheduled in the future.
        dispatch_pending_batch()
        event = OutboxEvent.objects.get(id=eid)
        assert event.status == OutboxEvent.FAILED
        assert event.retry_count == 1
        assert event.available_at > timezone.now()

        # Drive the remaining retries (resetting availability to avoid real sleeps).
        for _ in range(4):
            _reset_available(eid)
            dispatch_pending_batch()

        event = OutboxEvent.objects.get(id=eid)
        assert event.status == OutboxEvent.DLQ
        assert event.retry_count == 5
        dlq = OutboxEventDLQ.objects.get(original_event_id=eid)
        assert dlq.event_type == "test.Boom"
        assert dlq.failure_history

    def test_dlq_replay_and_resolve(self, temp_registry):
        @on_event("test.Boom")
        def boom(event):
            raise RuntimeError("nope")

        eid = EventBus.emit(event_type="test.Boom", idempotency_key="k1", payload={"x": 1})
        for _ in range(5):
            _reset_available(eid)
            dispatch_pending_batch()
        dlq = OutboxEventDLQ.objects.get(original_event_id=eid)

        new_id = replay_from_dlq(str(dlq.id))
        replayed = OutboxEvent.objects.get(id=new_id)
        assert replayed.status == OutboxEvent.PENDING
        assert replayed.payload == {"x": 1}
        dlq.refresh_from_db()
        assert dlq.resolved_at is not None

        # A different DLQ entry can be resolved without replay.
        dlq2 = OutboxEventDLQ.objects.create(
            original_event_id=uuid.uuid4(), event_type="test.X", payload={}, headers={}
        )
        resolve_dlq(str(dlq2.id), resolved_by=str(uuid.uuid4()), note="not a real issue")
        dlq2.refresh_from_db()
        assert dlq2.resolution_note == "not a real issue"


# ---------------------------------------------------------------------------
# End-to-end: the async daily-reward chain (login → outbox → dispatch → grant)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDailyRewardChain:
    def test_claim_requested_event_grants_reward(self):
        user_id = str(uuid.uuid4())
        economy_services.create_wallets_for_user(user_id=user_id)

        # This is exactly what identity.login emits.
        EventBus.emit(
            event_type="economy.DailyLoginRewardClaimRequested",
            idempotency_key=f"daily_login_claim:{user_id}:2026-06-05",
            payload={"user_id": user_id},
        )
        assert economy_services.get_balance(user_id=user_id, currency="MP") == Decimal("0.0000")

        dispatch_pending_batch()

        # The real economy.grant_daily_reward handler ran and credited MP.
        assert economy_services.get_balance(user_id=user_id, currency="MP") == Decimal("10.0000")
