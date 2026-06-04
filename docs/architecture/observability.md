# Observability

Three signals wired in from day one: **traces**, **logs**, **metrics**. Backend: self-hosted Grafana stack per ADR-0010.

---

## 1. Stack

| Signal | Component | Purpose |
|---|---|---|
| Traces | OpenTelemetry SDK → Tempo | Distributed trace storage |
| Logs | structlog → stdout → Promtail → Loki | Structured log aggregation |
| Metrics | Prometheus client → `/metrics` endpoints → Prometheus | Time-series metrics |
| Visualization | Grafana | Dashboards over all three |
| Alerts | Prometheus rules → Alertmanager → PagerDuty/Slack/Email | Notification routing |

All components run on the same server in V1 (see deployment.md).

---

## 2. Trace propagation

`trace_id` is generated at the edge (nginx, if header absent) and propagates through every layer:

```
HTTP request → X-Trace-Id header
        ↓
Django middleware → contextvar `trace_id`
        ↓
Django service.py → reads contextvar
        ↓
        ├─ gRPC client → metadata x-trace-id
        │       ↓
        │   gRPC service interceptor → contextvar
        │       ↓
        │   downstream gRPC / Outbox / log → carries trace_id
        │
        └─ Outbox.emit → event.headers["trace_id"]
                ↓
            Celery handler → header → contextvar → continues chain
```

Every span, every log line, every metric label can be filtered by trace_id.

### Implementation
- `libs/telemetry/` configures OpenTelemetry SDK
- DRF middleware: `TraceIdMiddleware` reads `X-Trace-Id` or generates UUID
- Celery: signal handlers transfer `trace_id` task header ↔ contextvar
- gRPC: interceptors at client and server inject/extract metadata

### Sampling

| Environment | Sampling rate | Override |
|---|---|---|
| local | 100% | — |
| staging | 100% | — |
| production | 10% | 100% on errors (any span sets error=true → trace is kept) |

---

## 3. Logging

### Format
Single line, structured JSON to stdout:
```json
{
  "ts": "2026-06-04T10:00:00.123Z",
  "level": "info",
  "service": "bcp-django",
  "trace_id": "...",
  "request_id": "...",
  "user_id": "...",
  "logger": "apps.economy.services",
  "msg": "wallet.credit",
  "wallet_id": "...",
  "amount": "10.0000",
  "currency": "MP"
}
```

### Required fields
- `ts`, `level`, `service`, `logger`, `msg`
- `trace_id` (auto-injected by `libs/logging/`)
- `request_id`
- `user_id` if present in context

### Levels
- `debug`: dev only, not shipped to Loki
- `info`: normal business activity
- `warn`: anomaly worth seeing but not paging
- `error`: failure; auto-creates Sentry breadcrumb
- `critical`: pager-worthy; auto-creates PagerDuty incident

### Sensitive data redaction
`libs/logging/` provides `redact()` helper. **Never log**:
- Passwords (hashes or plaintext)
- JWT access/refresh tokens
- Stripe / webhook secrets
- KYC document images or full numbers
- Full PII (email is OK; full name+address+DOB+phone combo is not)

### Shipping to Loki
- `promtail` tails journald, applies labels (`service=bcp-django` from `_SYSTEMD_UNIT`)
- Loki retention: 30 days; archive to cold storage after

---

## 4. Metrics

### RED for every RPC + HTTP endpoint
- **R**ate (requests/sec) → `http_requests_total{handler,method,status}` counter
- **E**rrors (error rate) → derive from same counter where `status >= 500`
- **D**uration (p50/p95/p99) → `http_request_duration_seconds{handler,method}` histogram

Same shape for gRPC: `grpc_requests_total`, `grpc_request_duration_seconds`.

### Business metrics (V1)

| Metric | Type | Labels |
|---|---|---|
| `wallet_credit_total` | counter | `currency`, `entry_type` |
| `wallet_debit_total` | counter | `currency`, `entry_type` |
| `outbox_pending_count` | gauge | `event_type` |
| `outbox_dispatch_lag_seconds` | gauge | — |
| `outbox_dlq_depth` | gauge | `event_type` |
| `outbox_handler_duration_seconds` | histogram | `handler` |
| `notification_send_total` | counter | `template`, `channel`, `status` |
| `payments_order_total` | counter | `business_kind`, `payment_provider`, `status` |
| `live_active_streams` | gauge | — |
| `live_active_viewers` | gauge | `stream_id` |
| `chat_active_streams` | gauge | — |
| `chat_message_total` | counter | — |
| `audit_records_total` | counter | `action`, `severity` |
| `audit_critical_action_total` | counter | `action` |

### Infrastructure metrics (V1)
- node_exporter on the server (CPU, memory, disk, network)
- postgres_exporter (connection count, slow queries, replication lag in V2)
- redis_exporter (memory, key count, eviction rate)
- blackbox_exporter for synthetic checks

---

## 5. Dashboards

Committed to repo under `ops/grafana/dashboards/`:

| Dashboard | What it shows |
|---|---|
| `platform-overview.json` | Overall request rate, error rate, key alerts |
| `economy.json` | Wallet credit/debit rates, reconciliation status, daily reward grants |
| `payments.json` | Order creation/paid/failed rates per provider |
| `outbox.json` | Outbox queue depth, dispatch lag, DLQ depth, per-event handler latency |
| `notification.json` | Send rate per template/channel, error rate per provider |
| `infrastructure.json` | Server resources, DB health, Redis health |
| `audit.json` | Critical/sensitive audit volume, anomaly indicators |

Dashboards are reviewed in PRs same as code. Changes via Grafana UI must be exported and committed back.

---

## 6. Alert rules

Rules in `ops/prometheus/alerts.yml`. Categorized by severity:

### Critical (pager)
- `bcp_django` not active for > 1 min
- `bcp_dispatcher` not active for > 1 min
- DLQ depth > 0 for any event type
- Wallet reconciliation mismatch detected
- Webhook signature invalid (any)
- `audit.AuditFailed` event in OutboxEvent
- Disk usage > 90%
- PostgreSQL down
- Redis down

### Warning (dashboard + email)
- gRPC error rate > 5% for 10 min
- HTTP 5xx rate > 1% for 10 min
- Outbox dispatch lag > 60s sustained 5 min
- Notification provider error rate > 5% per channel
- Memory > 90% sustained 5 min
- DB connection usage > 80%
- JWT key expiring in < 14 days

### Info (dashboard only)
- Outbox queue growing rate > 10/s
- Per-template notification volume spikes
- Live stream count change > 50% in 5 min

---

## 7. Tracing in practice (debugging)

To investigate "why did this user's order fail":

1. Mobile log shows error response with `trace_id`.
2. Grafana → Explore → Tempo → query trace_id → see full span tree:
   - HTTP `POST /api/v1/commerce/orders` → Django view
   - Django service → SELECT FOR UPDATE wallet
   - Django service → INSERT product_order
   - Django service → INSERT outbox_event
   - (transaction commits)
   - Dispatcher → SELECT pending
   - Celery task → gRPC NotificationService.Send
3. Loki query for same `trace_id` shows correlated log lines at each step.
4. Prometheus metric panels filter by trace-id correlation in dashboards.

The platform is debugged at the trace level, not the per-service log level.

---

## 8. SLO (V1)

V1 does NOT set hard SLOs. We collect baseline data for one quarter, then propose SLOs.

Indicators we track to inform future SLOs:
- API availability: % of requests returning < 500 status
- API latency: p95 of authorized endpoints
- Payment success rate: paid / (paid + failed within 5min)
- Notification delivery rate: delivered / queued (per channel)

---

## 9. Anti-patterns

- ❌ Logging full request/response bodies for content endpoints (volume)
- ❌ Per-user dashboards (privacy + scale)
- ❌ Using `print()` instead of `structlog`
- ❌ Skipping `trace_id` in custom-built scripts (use telemetry middleware)
- ❌ Alerting on every error (causes alert fatigue; aggregate to error rate)
- ❌ Dashboards built ad-hoc in Grafana UI without committing JSON
- ❌ Long-running queries against Loki/Tempo via Grafana (use proper retention + indices)
