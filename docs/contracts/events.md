# Events Contract (Outbox)

The platform's **event bus**. Every cross-app side effect, every async fan-out, every gRPC service trigger flows through this single mechanism.

**App**: `apps/events/`
**Related ADR**: ADR-0003 Transactional Outbox + Dispatcher + DLQ
**Status**: 🟢 V1 (W7 — required infrastructure before any business code can emit events)

---

## 1. Why this contract exists

Without a unified Events contract, every Django app invents its own:
- Event naming convention
- Payload field set
- Trace propagation strategy
- Idempotency key format
- Failure handling

Result: handlers can't reliably consume each other, observability breaks, and replays are unsafe. This document is the **single source of truth** for what an Outbox event looks like, how it's produced, and how it's consumed.

---

## 2. OutboxEvent table

```sql
CREATE TABLE outbox_event (
    id              UUID PRIMARY KEY,
    event_type      TEXT NOT NULL,        -- "<domain>.<PastTense>" e.g. "identity.UserRegistered"
    event_version   SMALLINT NOT NULL DEFAULT 1,
    idempotency_key TEXT NOT NULL,
    payload         JSONB NOT NULL,
    headers         JSONB NOT NULL,        -- trace_id, request_id, actor_id, brand_id (future), source_service
    status          TEXT NOT NULL DEFAULT 'pending',
                                          -- pending | dispatched | processed | failed | dlq
    retry_count     INT NOT NULL DEFAULT 0,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    dispatched_at   TIMESTAMPTZ,
    processed_at    TIMESTAMPTZ,
    available_at    TIMESTAMPTZ NOT NULL DEFAULT now(),  -- for backoff scheduling

    CONSTRAINT outbox_idempotency_unique UNIQUE (event_type, idempotency_key)
);

CREATE INDEX idx_outbox_pending ON outbox_event (status, available_at)
  WHERE status IN ('pending', 'failed');

CREATE INDEX idx_outbox_dispatched_unprocessed ON outbox_event (status, dispatched_at)
  WHERE status = 'dispatched';

CREATE INDEX idx_outbox_event_type_created ON outbox_event (event_type, created_at DESC);
```

### Status lifecycle

```
pending ──(dispatcher picks up)──→ dispatched ──(handler ack)──→ processed (terminal)
   ▲                                  │
   │                                  └─(handler fail)──→ failed (retry, with backoff)
   │                                                          │
   └──────────(retry available_at reached)────────────────────┘
                                                              │
                                       (retry_count >= 5)─────→ dlq (terminal; needs human)
```

### DLQ table

```sql
CREATE TABLE outbox_event_dlq (
    id              UUID PRIMARY KEY,
    original_event_id  UUID NOT NULL,
    event_type      TEXT NOT NULL,
    payload         JSONB NOT NULL,
    headers         JSONB NOT NULL,
    failure_history JSONB NOT NULL,        -- array of {timestamp, error, retry_count}
    moved_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ,
    resolved_by     UUID,                   -- admin user id
    resolution_note TEXT
);
```

---

## 3. Naming convention

`event_type` is **always** `<domain>.<PastTense>` or `<domain>.<sub>.<PastTense>`.

| ✅ Correct | ❌ Wrong | Why |
|---|---|---|
| `identity.UserRegistered` | `user_registered` | Domain prefix missing |
| `economy.WalletCredited` | `economy.CreditWallet` | Present tense / imperative |
| `content.video.Liked` | `VideoLiked` | Domain prefix missing |
| `content.live.GiftSent` | `live.gift_sent` | Inconsistent case |
| `payments.OrderPaid` | `payments.order_paid` | Use PascalCase for action |

Rules:
- Domain prefix lowercase, action PascalCase past-tense
- Sub-domain allowed: `content.video.*`, `content.drama.*`, `content.live.*`
- One verb only; no `AndAlso` / `OrSomething`
- Use past tense always (something happened); never future or imperative

---

## 4. Required payload + headers

### `headers` (every event, always present)

```json
{
  "trace_id": "<uuid>",
  "request_id": "<uuid>",
  "actor_id": "<user-uuid or service-account-id>",
  "source_service": "django" | "notification" | "chat" | "live_runtime",
  "occurred_at": "2026-06-04T10:00:00.123Z",
  "brand_id": null
}
```

- `trace_id`: propagated from incoming request (per conventions.md §10)
- `actor_id`: who caused this event — null for system-triggered, uuid for user-triggered
- `source_service`: which process emitted (for debugging)
- `occurred_at`: when business event happened (may differ from `created_at` on the row)
- `brand_id`: reserved for future multi-brand (always null in V1)

### `payload` (event-specific, structured)

Each event type defines its own payload schema. Common conventions:
- All `*_id` fields are UUID strings
- All money fields use `{amount, currency}` shape
- All timestamps are ISO 8601 UTC strings
- Never embed full objects — embed references (ids) and let consumers resolve

Example:
```json
{
  "user_id": "550e8400-...",
  "email": "user@example.com",
  "wallet_ids": {
    "point": "...",
    "credit": "..."
  }
}
```

---

## 5. Producer contract

### How to emit an event (the only correct way)

```python
# In a service.py method, inside the business transaction:
from apps.events.services import EventBus

with transaction.atomic():
    user = User.objects.create(...)
    wallet_point = PointWallet.objects.create(user=user, ...)
    wallet_credit = CreditWallet.objects.create(user=user, ...)

    EventBus.emit(
        event_type="identity.UserRegistered",
        idempotency_key=f"user_registered:{user.id}",
        payload={
            "user_id": str(user.id),
            "email": user.email,
            "wallet_ids": {
                "point": str(wallet_point.id),
                "credit": str(wallet_credit.id),
            },
        },
        actor_id=user.id,
    )
```

### What EventBus.emit does

1. Validates `event_type` matches naming convention (regex)
2. Validates `idempotency_key` is non-empty, ≤ 128 chars
3. Reads `trace_id` / `request_id` from current request context (or task header)
4. Inserts into `outbox_event` table with `status=pending`
5. Returns event id; raises if (event_type, idempotency_key) already exists

### Idempotency key conventions per domain

| Domain | Key shape | Example |
|---|---|---|
| identity | `<event>:<entity_id>` | `user_registered:<user_id>` |
| economy | `<event>:<wallet_id>:<ledger_id>` | `wallet_credited:<wallet_id>:<ledger_entry_id>` |
| payments | `<event>:<order_no>:<status>` | `order_paid:ORD-2026-...:PAID` |
| content | `<event>:<content_id>:<actor_id>` | `video_liked:<video_id>:<user_id>` |
| commerce | `<event>:<order_no>:<state>` | `order_shipped:ORD-2026-...:SHIPPING` |
| live broadcast | `<event>:<stream_id>:<seq>` | `gift_sent:<stream_id>:<gift_tx_id>` |
| platform | `<event>:<config_version>` | `config_updated:v42` |

**Idempotency key is the dedup primitive.** Same key = same event = handler runs exactly once.

---

## 6. Dispatcher

`apps/events/dispatcher.py` is a **separate process** (systemd unit `bcp-dispatcher`).

### Behavior
1. Acquires PG advisory lock (`pg_try_advisory_lock(<DISPATCHER_LOCK_ID>)`) — leader election
2. Polls `outbox_event WHERE status IN ('pending', 'failed') AND available_at <= now()` ordered by `created_at`
3. Batch size: 100; poll interval: 250ms when pending exists, 2s when empty
4. For each event:
   - Lookup handlers in registry by `event_type` (multiple handlers allowed)
   - Schedule Celery task for each handler
   - Mark row `status=dispatched`, `dispatched_at=now()`
5. If Celery task fails:
   - `status=failed`, `retry_count += 1`, `last_error=<exception>`, `available_at = now() + backoff(retry_count)`
   - Backoff: 5s, 30s, 2m, 10m, 30m (exponential with cap)
6. If `retry_count >= 5`: move to `outbox_event_dlq`, alert fires

### Handler ack
When the Celery task completes successfully, it calls:
```python
EventBus.ack(event_id, handler_name)
```
Which inserts a row in `outbox_event_handler_ack(event_id, handler_name, processed_at)`. The dispatcher checks all required handlers acked before marking the event itself `processed`.

### Single-leader requirement
Only one dispatcher runs at a time (advisory lock). Standby dispatcher polls the lock every 5s.

---

## 7. Consumer contract (Python handlers)

### Registration

```python
# apps/identity/handlers.py
from apps.events.registry import on_event

@on_event("identity.UserRegistered")
def send_welcome_email(event):
    """Triggers NotificationService gRPC call."""
    payload = event.payload
    notification_client.send(
        idempotency_key=f"welcome:{event.id}",
        template_code="welcome",
        recipient={"user_id": payload["user_id"]},
    )
```

### Handler requirements

1. **Idempotent**: must handle being called multiple times with same `event.id` (dispatcher may re-deliver after timeout)
2. **Pure**: no side effects other than the documented downstream action
3. **Time-bounded**: must complete in <30s or raise; long work goes to a follow-up event
4. **Trace-aware**: must propagate `event.headers["trace_id"]` to downstream gRPC calls
5. **Error semantics**:
   - Raise → dispatcher records failure, retries with backoff
   - Return → success ack
   - Special `SkipHandler` exception → mark this handler ack'd without action (e.g., already-processed detected via idempotency on receiver)

### Handler-event mapping (subset; full catalog in §10)

| Event | Handler module | Action |
|---|---|---|
| `identity.UserRegistered` | `apps.identity.handlers.send_welcome_email` | NotificationService.Send |
| `identity.PasswordResetRequested` | `apps.identity.handlers.send_reset_email` | NotificationService.Send |
| `economy.DailyLoginRewardClaimRequested` | `apps.economy.handlers.grant_daily_reward` | EconomyService.credit |
| `payments.OrderPaid` | (dispatches by business_kind) | varies |
| `commerce.OrderPaid` | `apps.commerce.handlers.fulfill_order` | reserve / ship / notify |
| `commerce.OrderShipped` | `apps.commerce.handlers.notify_buyer_shipped` | NotificationService.Send |
| `content.live.GiftSent` | `apps.content.live.handlers.broadcast_gift` | LiveRuntimeService.BroadcastGift gRPC |
| `content.live.ChatMessagePosted` | same | LiveRuntimeService.BroadcastChat gRPC |
| `membership.MembershipExpired` | `apps.membership.handlers.notify_expired` | NotificationService.Send |
| `economy.WalletReconciliationMismatch` | `apps.economy.handlers.alert_ops` | Alert + DLQ-style escalation |

---

## 8. Consumer contract (gRPC services)

External services (Notification, Chat, Live Runtime) can subscribe to events without being Celery handlers:

```proto
service EventStream {
  rpc Subscribe(SubscribeRequest) returns (stream EventEnvelope);
  rpc Ack(AckRequest) returns (AckResponse);
}
```

Pattern:
- Service opens long-lived stream with `subscriber_id` + list of `event_type` patterns
- Django dispatcher fans out matching events
- Service processes + calls `Ack(event_id)`
- Unacked events for that subscriber are redelivered on reconnect

V2: this gRPC streaming model. V1: services subscribe indirectly via Celery handlers that call them via gRPC RPC.

---

## 9. Failure modes & operational rules

| Failure | Detection | Response |
|---|---|---|
| Dispatcher process dies | Heartbeat lag > 30s | Standby takes advisory lock; pager |
| Specific handler failing en masse | DLQ depth growing | Pager + runbook: `dlq-growth.md` |
| Outbox table bloat | Row count > 100k | Archive job: move `processed > 30d` to cold storage |
| Idempotency key collision | INSERT fails | EventBus.emit raises `EventAlreadyEmitted`; caller decides (usually swallow + log) |
| Trace_id missing | EventBus.emit log warning | Auto-generate, but tag event for investigation |
| Handler exceeds deadline | Celery task timeout | Mark failed; retry with backoff |
| gRPC service unreachable | Handler raises `UNAVAILABLE` | Backoff retry; circuit breaker (per grpc-integration.md) |

### Metrics

| Metric | Type | Alert |
|---|---|---|
| `outbox_pending_count` | gauge | > 1000 sustained 5m |
| `outbox_dispatch_lag_seconds` | gauge | > 60 sustained 5m |
| `outbox_dlq_depth` | gauge | > 0 (any) |
| `outbox_emit_rate{event_type}` | counter | spike anomaly |
| `outbox_handler_duration_seconds{handler}` | histogram | p99 > 10s |
| `outbox_handler_error_rate{handler}` | gauge | > 5% over 10m |

---

## 10. Full event catalog (V1+V2+V3)

### Identity (12)

| Event | When | Required payload |
|---|---|---|
| `identity.UserRegistered` | After register success | `user_id, email, wallet_ids` |
| `identity.UserLoggedIn` | After login success | `user_id, session_id, ip_address` |
| `identity.PasswordResetRequested` | After reset request | `user_id, email, reset_token_id` |
| `identity.PasswordChanged` | After change/confirm | `user_id` |
| `identity.ProfileUpdated` | After profile PATCH | `user_id, changed_fields` |
| `identity.KycSubmitted` | After submit | `user_id, kyc_profile_id` |
| `identity.KycApproved` | After admin approval | `user_id, kyc_profile_id, reviewer_id` |
| `identity.KycRejected` | After admin rejection | `user_id, kyc_profile_id, reviewer_id, reason` |
| `identity.KycResubmitted` | After upload on approved | `user_id, kyc_profile_id` |
| `identity.UserFollowed` | After follow | `follower_id, target_user_id` |
| `identity.UserUnfollowed` | After unfollow | same |
| `identity.CreatorPromoted` | After seller-app approval grants creator role | `user_id, application_id` |

### Economy (8)

| Event | When | Required payload |
|---|---|---|
| `economy.WalletCredited` | Every credit | `wallet_id, user_id, ledger_id, entry_type, amount, currency, idempotency_key` |
| `economy.WalletDebited` | Every debit | same |
| `economy.DailyLoginRewardClaimRequested` | Login emits | `user_id, date` |
| `economy.DailyLoginRewardGranted` | After grant | `user_id, ledger_id, amount, currency` |
| `economy.CreditRechargeCreated` | After POST /credit-recharges | `recharge_id, user_id, order_no` |
| `economy.CreditRechargeFulfilled` | After credit posted | `recharge_id, user_id, ledger_id, amount, currency` |
| `economy.CreditRedeemRequested` | After POST /credit-redeems | `redeem_id, user_id, amount, currency, redeem_method` |
| `economy.WalletReconciliationMismatch` | Reconciliation failure | `wallet_id, expected, actual, divergence_at` |

Note: MP purchase events removed (MP is earned-only).

### Payments (9)

| Event | When | Required payload |
|---|---|---|
| `payments.OrderCreated` | After create | `order_no, business_kind, business_ref_id, user_id, amount, currency, payment_provider` |
| `payments.OrderAuthorized` | Stripe auth | `order_no, intent_id` |
| `payments.OrderPaid` | PAID transition | `order_no, business_kind, business_ref_id, paid_at` |
| `payments.OrderFailed` | FAILED | `order_no, reason` |
| `payments.OrderExpired` | EXPIRED | `order_no` |
| `payments.OrderCancelled` | CANCELLED | `order_no, cancel_reason, actor_id` |
| `payments.OrderRefundInitiated` | Refund start | `order_no, refund_id, amount, currency` |
| `payments.OrderRefunded` | REFUNDED | `order_no, refund_id, refunded_at` |
| `payments.WebhookReceived` | Every webhook (audit) | `provider, event_id, signature_valid, raw_payload_hash` |

### Content — Video (9)

| Event |
|---|
| `content.video.Created` |
| `content.video.Updated` |
| `content.video.Deleted` |
| `content.video.Liked` |
| `content.video.Unliked` |
| `content.video.Commented` |
| `content.video.Shared` |
| `content.video.Viewed` (sampled — only one in N to manage volume) |
| `content.video.Gifted` |

### Content — Drama (14)

| Event |
|---|
| `content.drama.SeriesCreated` |
| `content.drama.SeriesUpdated` |
| `content.drama.SeriesDeleted` |
| `content.drama.EpisodeCreated` |
| `content.drama.EpisodeUpdated` |
| `content.drama.EpisodeDeleted` |
| `content.drama.EpisodeUnlocked` |
| `content.drama.SeriesFavorited` |
| `content.drama.SeriesUnfavorited` |
| `content.drama.SeriesCommented` |
| `content.drama.SeriesShared` |
| `content.drama.SeriesViewed` (sampled) |
| `content.drama.Gifted` |
| `content.drama.ProgressUpdated` (sampled — high volume) |

### Content — Live (10)

| Event | Notes |
|---|---|
| `content.live.StreamCreated` | — |
| `content.live.StreamStarted` | Triggers follower-notification fan-out |
| `content.live.StreamEnded` | — |
| `content.live.StreamFailed` | Alerts |
| `content.live.GiftSent` | **Triggers `LiveRuntimeService.BroadcastGift` gRPC** |
| `content.live.ChatMessagePosted` | **Triggers `LiveRuntimeService.BroadcastChat` gRPC** |
| `content.live.ChatMessageDeleted` | Triggers broadcast deletion |
| `content.live.ChatMessagePinned` | Triggers broadcast pin |
| `content.live.ViewerJoined` | Emitted by Live Runtime → Django (gRPC stream) |
| `content.live.ViewerLeft` | Same |

### Commerce (19)

| Event |
|---|
| `commerce.OrderCreated` |
| `commerce.OrderPaid` |
| `commerce.OrderShipped` |
| `commerce.OrderCompleted` |
| `commerce.OrderCancelled` |
| `commerce.OrderSettled` |
| `commerce.RefundRequested` |
| `commerce.RefundApproved` |
| `commerce.RefundRejected` |
| `commerce.RefundCompleted` |
| `commerce.SellerApplicationSubmitted` |
| `commerce.SellerApplicationApproved` |
| `commerce.SellerApplicationRejected` |
| `commerce.StoreCreated` |
| `commerce.ProductCreated` |
| `commerce.ProductUpdated` |
| `commerce.ProductArchived` |
| `commerce.CartItemAdded` |
| `commerce.CartItemRemoved` |

### Membership (12)

| Event |
|---|
| `membership.OrderCreated` |
| `membership.OrderPaid` |
| `membership.OrderCancelled` |
| `membership.MembershipGranted` |
| `membership.MembershipExpired` |
| `membership.MembershipCancelled` |
| `membership.ManualTxHintSubmitted` |
| `membership.ManualTxHintVerified` |
| `membership.SubscriptionCreated` |
| `membership.SubscriptionRenewed` |
| `membership.SubscriptionPastDue` |
| `membership.SubscriptionCancelled` |

### Platform (2)

| Event |
|---|
| `platform.ConfigUpdated` |
| `platform.FeatureToggled` |

### Audit (1 meta-event)

| Event |
|---|
| `audit.AuditFailed` (emitted when a required audit write itself failed — alerts) |

**Total: ~96 event types.**

---

## 11. Event versioning

`event_version` starts at 1. Bump when payload schema changes incompatibly.

Rules:
- Additive change (new optional field): same version
- Removed / renamed / type-changed field: bump version
- Consumers MUST declare which versions they handle:
  ```python
  @on_event("identity.UserRegistered", versions=[1, 2])
  ```
- Producer can publish multiple versions during migration window

---

## 12. Querying events (admin/debug)

### GET /api/v1/admin/events/outbox 🛠 Admin

Cursor-paginated list with filters: `event_type`, `status`, `actor_id`, `date_from`, `date_to`.

### GET /api/v1/admin/events/outbox/{event_id} 🛠 Admin

Single event + all handler ack records.

### POST /api/v1/admin/events/dlq/{dlq_id}/replay 🛠 Admin

Move a DLQ entry back to `outbox_event` with `retry_count=0` for re-processing.

### POST /api/v1/admin/events/dlq/{dlq_id}/resolve 🛠 Admin

Mark a DLQ entry as resolved (won't retry) with a reason note.

---

## 13. Retention

| Status | Retention |
|---|---|
| `processed` | 30 days hot, then archive to cold storage |
| `failed` | 90 days hot (for debugging) |
| `dlq` (unresolved) | indefinite |
| `dlq` (resolved) | 1 year, then archive |

Archival via nightly Celery beat job: `archive_processed_outbox_rows`.

---

## 14. V1 deliverables (W7)

- [ ] `apps/events/` skeleton + models
- [ ] `EventBus.emit` service-layer API
- [ ] Dispatcher process (systemd unit + advisory lock + leader election)
- [ ] Handler registry + Celery task wrapper
- [ ] DLQ table + retry/backoff logic
- [ ] Admin endpoints (list, replay, resolve)
- [ ] Metrics export
- [ ] Alert rules (DLQ depth, dispatch lag)
- [ ] First event end-to-end: `identity.UserRegistered` → `send_welcome_email` handler → NotificationService gRPC stub

After this lands, every other app can start emitting.

---

## 15. Anti-patterns (do not do)

- ❌ Call `task.delay()` directly from business code (use Outbox)
- ❌ Skip `idempotency_key` (every event must have one)
- ❌ Embed full DB rows in `payload` (use ids, let consumers resolve)
- ❌ Emit events outside `transaction.atomic()` (event must commit with business write or roll back together)
- ❌ Use event for synchronous request-reply (use gRPC RPC)
- ❌ Write event_type strings inline (use constants in `apps/events/types.py`)
- ❌ Catch and swallow handler exceptions (let dispatcher retry/DLQ)
- ❌ Re-emit the same event from a handler (create a new event with new idempotency_key for chained workflows)
