# ADR-0010: Observability backend — Grafana stack (self-hosted)

## Status
Accepted

## Context
We need three observability signals from day one: traces, logs, metrics. Two viable backends:

1. **Datadog / Honeycomb / similar SaaS**: turn-key, polished UX, fast to onboard. Cost scales steeply with data volume and team size.
2. **Grafana stack** (Loki for logs, Tempo for traces, Prometheus for metrics, Grafana for UI): self-hosted, open source, no per-engineer fees. Ops responsibility on us.

Team size is small (1-3 engineers). Cost sensitivity is high. Operational burden of self-hosting Grafana stack is moderate (well-documented; many teams run it).

## Decision

### V1: Grafana stack, self-hosted on the same server as the platform
- **Loki** — logs (structured JSON ingested via Promtail tailing journald)
- **Tempo** — distributed traces (OpenTelemetry exporter)
- **Prometheus** — metrics (scrape `:9090` endpoints on Django + each gRPC service)
- **Grafana** — UI (dashboards committed to repo under `ops/grafana/`)
- **Alertmanager** — alert routing (to email / PagerDuty / Slack webhook)

### OpenTelemetry SDK
- Standard SDK in Django and all gRPC services
- `trace_id` propagation per `contracts/conventions.md §10`
- Span sampling: 100% in dev, 100% on errors, 10% otherwise in prod (configurable)

### Migration path
If self-hosted ops becomes the bottleneck OR team grows past 5 engineers:
- Re-evaluate against Datadog / Honeycomb
- OpenTelemetry-based instrumentation is portable; swap exporter, not code

## Anti-decision
We do NOT:
- **Roll our own log/metric aggregation** (no custom ingestion service)
- **Run separate ELK stack** (Elasticsearch ops is heavier than Loki for our scale)
- **Skip distributed tracing**: in a system with 1 monolith + 3 gRPC services + Celery + WebSocket gateways, "grep across services" is not a viable debugging strategy.
- **Use Sentry as the primary telemetry backend**: Sentry is excellent for error tracking, but tracing/metrics/log aggregation are not its core. Keep Sentry as a complementary error tool only.

## Consequences

**Good**
- Zero per-engineer fees; observability cost = server resources only
- Dashboards live in repo (versioned, reviewed)
- OpenTelemetry-based instrumentation is portable if we later switch
- Self-hosted retention policy under our control

**Bad**
- Operational burden: someone must keep Grafana stack up; documented in `ops/runbooks/`
- During Grafana outage, we're blind; mitigated by simple alerting that doesn't depend on Grafana (Prometheus → Alertmanager → email)
- UI is less polished than Datadog; team will adapt

**Neutral**
- Sentry retained for error tracking (cheap tier covers most use)
