# Runbooks

Operational procedures for known incidents. Each runbook follows the same shape:

1. **Symptom** — what you observe
2. **Severity** — who to page
3. **Investigate** — ordered steps
4. **Mitigate** — stop the bleeding
5. **Resolve** — fix root cause
6. **Post-incident** — what to write up

## Index

- [dlq-growth.md](dlq-growth.md) — Outbox DLQ depth > 0
- [wallet-reconciliation.md](wallet-reconciliation.md) — wallet balance mismatch
- [jwt-key-rotation.md](jwt-key-rotation.md) — scheduled + emergency JWT key rotation
- [dispatcher-stuck.md](dispatcher-stuck.md) — Outbox dispatcher not advancing
- [service-degraded.md](service-degraded.md) — gRPC service degraded or down

## Adding a runbook

After any incident, the post-mortem owner decides whether the response pattern should become a runbook. If yes:
- Use the same shape
- Keep it short enough to read at 3 a.m.
- Concrete commands, not philosophy
- Link to dashboards and alert rules

## Operating discipline

- Runbooks are **opinions** for the on-call engineer; experience is final
- If a runbook says "X always works" and X stops working, that's a runbook bug — open a PR after the incident
- All runbook executions should leave an audit trail (`platform.config.*` if config touched, manual notes if not)
