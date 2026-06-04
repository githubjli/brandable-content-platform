# ADR-0003: Transactional Outbox with independent Dispatcher

## Status
Accepted

## Context
We need reliable async side effects: a debit must reliably trigger notifications, leaderboard updates, audit fans, gRPC service broadcasts. Two naive options fail:

1. **Direct `task.delay()` inside transaction**: the task runs even if the transaction rolls back. Money + side effect can drift.
2. **`task.delay()` after commit hook**: if the broker is down between commit and dispatch, the side effect is lost forever with no record.

## Decision
**Transactional Outbox**:

1. Business code writes business rows AND an `OutboxEvent` row in the **same DB transaction**.
2. A separate process (the **Dispatcher**, systemd unit `bcp-dispatcher`) polls `OutboxEvent` for `pending` rows, dispatches the corresponding Celery task, and marks the row `dispatched`.
3. Celery handlers ack each event by writing to `OutboxEventHandlerAck`. The dispatcher marks the event `processed` after all required handlers ack.
4. Failed dispatches retry up to 5 times with exponential backoff (5s → 30s → 2m → 10m → 30m). Permanently failed events move to `OutboxEventDLQ`. DLQ depth > 0 fires an alert.

The Dispatcher uses Postgres advisory locks (`pg_try_advisory_lock`) for single-leader election; a standby instance polls every 5s.

Full schema and behavior in `contracts/events.md`.

## Anti-decision
We do NOT use:
- **Kafka or any external event log**: ops overhead disproportionate to scale; Postgres handles V1 throughput.
- **Django signals for cross-app events**: signals are sync within the request and can't span processes; they're code-coupling without durability guarantees.
- **Celery as the durability layer**: Celery is the work queue; the durability layer must be transactional with business writes (DB).
- **`atexit` / post-commit hooks** as a "good enough" durability replacement: doesn't survive process crash mid-commit.

## Consequences

**Good**
- Side effects are durable
- Replays are safe (idempotency_key)
- Same primitive serves Notification triggers, gRPC service broadcasts, analytics fan-out

**Bad**
- One extra process to operate (Dispatcher) with leader-election complexity
- Polling latency: target <1s, acceptable for non-critical async work
- Outbox table needs periodic archival (processed rows pruned at 30 days)

**Neutral**
- Money-touching writes stay synchronous in the business transaction; only side effects are async
