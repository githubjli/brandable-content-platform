# Runbook: DLQ Growth

## Symptom
`outbox_dlq_depth` metric > 0 and growing, or `OutboxEventDLQ` table has new rows.

## Severity
- DLQ depth > 0 for any window: **page on-call**
- DLQ depth > 100: **critical, escalate to platform team**

## Investigate

Find the failing event types:
```sql
SELECT event_type, COUNT(*), MAX(moved_at), MIN(moved_at)
FROM outbox_event_dlq
WHERE resolved_at IS NULL
GROUP BY event_type
ORDER BY COUNT(*) DESC;
```

Inspect a sample:
```sql
SELECT id, event_type, payload, failure_history
FROM outbox_event_dlq
WHERE resolved_at IS NULL
ORDER BY moved_at DESC
LIMIT 10;
```

Common causes:
- Downstream service permanently broken (check service health dashboard)
- Schema mismatch (handler expecting field that doesn't exist)
- Auth misconfiguration (JWT verification failing on the receiver side)
- Bad payload (data quality issue upstream)
- Stripe webhook signature drift (recent provider key rotation not picked up)

## Mitigate

If a specific event type is failing en masse, disable that handler temporarily:

Toggle feature flag:
```bash
# via admin UI or:
curl -X PATCH https://api.../api/v1/admin/feature-flags/<handler-flag-key> \
  -H "Authorization: Bearer <admin-jwt>" \
  -d '{"enabled": false, "reason": "DLQ growth incident <id>"}'
```

This keeps new events from joining DLQ; existing DLQ rows wait until resolved.

For a runaway producer (events accumulating fast):
```bash
# Temporarily increase dispatcher concurrency
sudo systemctl edit bcp-dispatcher
# Add: Environment="DISPATCHER_CONCURRENCY=20"
sudo systemctl restart bcp-dispatcher
```

## Resolve

1. Fix the root cause (deploy handler fix, fix downstream config, rotate webhook secret).
2. Replay DLQ rows:

```bash
# Dry run first
sudo -u bcp /opt/bcp/venv/bin/python /opt/bcp/django/manage.py replay_dlq \
  --event-type=<EventType> --limit=10 --dry-run

# Then real
sudo -u bcp /opt/bcp/venv/bin/python /opt/bcp/django/manage.py replay_dlq \
  --event-type=<EventType> --limit=100
```

Replays move rows back to `outbox_event` with `retry_count=0`.

3. Verify dispatcher processes them and they don't bounce back to DLQ:
```sql
SELECT status, COUNT(*) FROM outbox_event 
WHERE event_type='<EventType>' 
  AND created_at > now() - interval '10 min'
GROUP BY status;
```

4. Re-enable disabled handler.

## Post-incident
- If single bad event: note in incident log, no further action.
- If systemic: write post-mortem covering why retries didn't recover, what missing test would have caught it.
- Update test suite to cover the failure case.
- Consider per-event-type isolation in dispatcher (so one bad type doesn't starve others).

## Dashboards
- Grafana → Outbox dashboard
- Loki: `{service="bcp-dispatcher", level="error"}`
- Tempo: filter traces by `service=bcp-dispatcher` for failing event types
