# NotificationService gRPC Contract

The **first gRPC service** (V1 canary at W9). Handles email, SMS, push notifications via external providers.

**Service location**: `services/notification/`
**Implementation**: Python (grpcio + asyncio) in V1; potentially Go rewrite later
**Proto package**: `notification.v1`

---

## Why this service is the canary

Per ADR-0006:
1. **Lowest business complexity**: fan-out to providers, stateless
2. **Lowest failure cost**: missed email can be retried via Outbox
3. **Simplest RPC shape**: single-direction request/response
4. **Validates the whole stack**: proto pipeline + auth + tracing + deployment + observability

Once Notification is live, Chat and Live Runtime are "add business logic" not "build infrastructure."

---

## 1. Proto definition

`proto/notification/v1/notification.proto`:

```proto
syntax = "proto3";

package notification.v1;

import "common/v1/common.proto";
import "google/protobuf/timestamp.proto";

service NotificationService {
  // Health check
  rpc Ping(PingRequest) returns (PingResponse);

  // Send a notification (idempotent)
  rpc Send(SendRequest) returns (SendResponse) {
    option (idempotency.level) = IDEMPOTENT;
  }

  // Get delivery status
  rpc GetStatus(GetStatusRequest) returns (GetStatusResponse);

  // List recent notifications (admin/debug)
  rpc ListRecent(ListRecentRequest) returns (ListRecentResponse);
}

// --- Send ---

message SendRequest {
  string idempotency_key = 1;        // REQUIRED, max 128 chars
  string template_code = 2;           // e.g., "welcome", "password_reset", "kyc_approved"
  Recipient recipient = 3;
  map<string, string> context = 4;    // template variables
  Channel channel = 5;
  string trace_id = 6;
}

message Recipient {
  oneof identity {
    string user_id = 1;       // resolves to user's preferred channel
    string email = 2;
    string phone = 3;
    string push_token = 4;
  }
}

enum Channel {
  CHANNEL_UNSPECIFIED = 0;
  CHANNEL_EMAIL = 1;
  CHANNEL_SMS = 2;
  CHANNEL_PUSH = 3;
  CHANNEL_AUTO = 4;     // service picks based on user preferences
}

message SendResponse {
  string notification_id = 1;
  Status status = 2;
  google.protobuf.Timestamp accepted_at = 3;
}

enum Status {
  STATUS_UNSPECIFIED = 0;
  STATUS_QUEUED = 1;         // accepted, will deliver asynchronously
  STATUS_SENT = 2;           // sent to provider
  STATUS_DELIVERED = 3;      // confirmed delivery (when provider supports)
  STATUS_FAILED = 4;
  STATUS_BOUNCED = 5;
  STATUS_SKIPPED_USER_OPTED_OUT = 6;
}

// --- Get status ---

message GetStatusRequest {
  oneof key {
    string notification_id = 1;
    string idempotency_key = 2;
  }
}

message GetStatusResponse {
  string notification_id = 1;
  Status status = 2;
  string provider_message_id = 3;
  string error_message = 4;
  google.protobuf.Timestamp created_at = 5;
  google.protobuf.Timestamp delivered_at = 6;
}

// --- List recent ---

message ListRecentRequest {
  string user_id = 1;          // optional filter
  int32 limit = 2;             // max 100
  string cursor = 3;
}

message ListRecentResponse {
  repeated NotificationRecord results = 1;
  string next_cursor = 2;
}

message NotificationRecord {
  string notification_id = 1;
  string user_id = 2;
  string template_code = 3;
  Channel channel = 4;
  Status status = 5;
  google.protobuf.Timestamp created_at = 6;
}

// --- Health ---

message PingRequest {}

message PingResponse {
  string version = 1;
  google.protobuf.Timestamp server_time = 2;
}
```

---

## 2. RPC behavior

### Send
- **Idempotent**: same `idempotency_key` within 24h returns cached response.
- **Asynchronous delivery**: returns `STATUS_QUEUED` immediately; actual provider call happens in background worker.
- **Deadline**: 3s default (Django caller); server tries to enqueue within deadline.
- **Errors**:
  - `INVALID_ARGUMENT` — missing required fields, invalid template_code
  - `NOT_FOUND` — user_id doesn't exist
  - `UNAUTHENTICATED` — JWT invalid
  - `PERMISSION_DENIED` — service account lacks scope
  - `RESOURCE_EXHAUSTED` — internal queue full (backpressure signal)
  - `INTERNAL` — provider config error

### GetStatus
- Read-only lookup
- Returns up-to-date status (server polls providers as available)

### ListRecent
- Admin / debug endpoint
- Requires `service.admin` scope on service account

### Ping
- No auth required (health checks)
- Returns version and time

---

## 3. Templates

Templates stored centrally (DB-backed in V1, Git-managed text files OK initially).

| Template code | Channels | Trigger event |
|---|---|---|
| `welcome` | EMAIL | `identity.UserRegistered` |
| `password_reset` | EMAIL | `identity.PasswordResetRequested` |
| `password_changed` | EMAIL | `identity.PasswordChanged` |
| `kyc_submitted` | EMAIL | `identity.KycSubmitted` |
| `kyc_approved` | EMAIL | `identity.KycApproved` |
| `kyc_rejected` | EMAIL | `identity.KycRejected` |
| `daily_reward_granted` | PUSH (V2) | `economy.DailyLoginRewardGranted` |
| ~~`point_purchase_completed`~~ | 🚫 N/A (MP is earned-only; no purchase event exists) |
| `credit_recharge_completed` | EMAIL | `economy.CreditRechargeFulfilled` |
| `order_created` | EMAIL | `commerce.OrderCreated` |
| `order_paid` | EMAIL | `commerce.OrderPaid` |
| `order_shipped` | EMAIL + PUSH (V2) | `commerce.OrderShipped` |
| `order_completed` | EMAIL | `commerce.OrderCompleted` |
| `membership_granted` | EMAIL | `membership.MembershipGranted` |
| `membership_expiring` | EMAIL | scheduled (7 days before expiry) |
| `gift_received` | PUSH (V2) | `content.*.Gifted` (receiver) |
| `live_started` | PUSH (V2) | `content.live.StreamStarted` (followers) |
| `seller_application_approved` | EMAIL | `commerce.SellerApplicationApproved` |
| `seller_application_rejected` | EMAIL | `commerce.SellerApplicationRejected` |

V1 ships email-only. SMS and PUSH templates documented but channels disabled until V2.

---

## 4. Providers

| Provider | Channels | V1 | V2 |
|---|---|---|---|
| SendGrid | EMAIL | 🟢 | |
| SES | EMAIL | (fallback) | |
| Twilio | SMS | | 🟡 |
| FCM (Firebase) | PUSH (Android) | | 🟡 |
| APNs | PUSH (iOS) | | 🟡 |

Provider config managed in `services/notification/config/` (env-driven, secrets in Doppler/AWS).

---

## 5. Architecture

```
Django (caller)
  │
  │ OutboxEvent: identity.UserRegistered
  ▼
Outbox Dispatcher
  │
  │ Celery task: send_welcome_email
  ▼
Celery worker
  │
  │ gRPC SendRequest (template=welcome, ...)
  ▼
NotificationService
  │
  │ Accept + enqueue (returns STATUS_QUEUED)
  ▼
NotificationWorker (inside service)
  │
  │ Render template + call provider
  ▼
SendGrid / Twilio / FCM
  │
  │ Provider response
  ▼
Update Notification record: STATUS_SENT / STATUS_FAILED
```

---

## 6. Authentication

- Caller (Django) uses **service account JWT** with scope `notification.send`
- Notification service validates JWT via shared JWKS endpoint
- Per ADR-0005, service accounts never use end-user tokens

---

## 7. Observability

- All RPCs traced via OpenTelemetry
- Metrics per `(template_code, channel, status)`:
  - `notification_send_total{template, channel, status}` — counter
  - `notification_send_latency_seconds{template, channel}` — histogram
  - `notification_queue_depth` — gauge
- Alert: any template with `error_rate > 5%` for 10 min

---

## 8. Storage

Internal DB (the service owns its data, per ADR-0006):

```sql
CREATE TABLE notification (
  id UUID PRIMARY KEY,
  idempotency_key TEXT UNIQUE NOT NULL,
  template_code TEXT NOT NULL,
  channel TEXT NOT NULL,
  user_id UUID,
  recipient_email TEXT,
  recipient_phone TEXT,
  status TEXT NOT NULL,
  context_json JSONB NOT NULL,
  provider_message_id TEXT,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  sent_at TIMESTAMPTZ,
  delivered_at TIMESTAMPTZ
);

CREATE INDEX idx_notification_user_created ON notification (user_id, created_at DESC);
CREATE INDEX idx_notification_status ON notification (status) WHERE status IN ('QUEUED', 'SENT');
```

Retention: 90 days for delivered/failed; 30 days for queued (failures move to DLQ after 5 retries).

---

## 9. V1 deliverables (W9)

- [ ] `services/notification/` skeleton with `Ping` RPC
- [ ] Proto generation in CI
- [ ] gRPC server with auth interceptor
- [ ] OpenTelemetry tracing
- [ ] SendGrid adapter
- [ ] `welcome`, `password_reset`, `password_changed` templates
- [ ] DB schema + migration
- [ ] systemd unit + nginx config
- [ ] End-to-end test: register → OutboxEvent → Celery → gRPC → SendGrid sandbox → DB record
- [ ] Monitoring dashboard (metrics + alerts)

---

## 10. V2/V3 deliverables

| Feature | V2 | V3 |
|---|---|---|
| SMS via Twilio | 🟡 | |
| Push via FCM/APNs | 🟡 | |
| User opt-in management (per channel, per template) | 🟡 | |
| Provider failover | 🟡 | |
| Template editor (admin UI) | | 🔵 |
| A/B testing of templates | | 🔵 |
| Scheduled notifications | | 🔵 |
