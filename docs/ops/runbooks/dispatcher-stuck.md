# Runbook: Outbox Dispatcher Stuck

## Symptom
- `outbox_pending_count` growing without `outbox_dispatch_lag_seconds` decreasing
- Side effects (emails, broadcasts, notifications) not happening
- `outbox_dispatch_lag_seconds` > 60 sustained

## Severity
- Lag > 60s: **page on-call**
- Lag > 300s: **critical**
- Lag > 600s + DLQ growing: **multi-page**

## Investigate

### 1. Is the dispatcher process up?

```bash
systemctl status bcp-dispatcher
journalctl -u bcp-dispatcher --since "10 min ago" -n 200
```

### 2. Is the advisory lock held by a dead process?

```sql
SELECT * FROM pg_locks WHERE locktype = 'advisory';
SELECT pid, application_name, state, query_start
FROM pg_stat_activity
WHERE pid IN (SELECT pid FROM pg_locks WHERE locktype = 'advisory');
```

If the lock is held by a `pg_stat_activity` row with `state='idle'` for hours, that's the issue — the dispatcher died but the lock was never released.

### 3. Is Redis broker reachable?

```bash
redis-cli -h localhost ping
redis-cli -h localhost INFO clients
```

### 4. Is the bottleneck dispatcher or workers?

```sql
-- Pending: events waiting for dispatcher
SELECT COUNT(*) FROM outbox_event WHERE status = 'pending';

-- Dispatched-but-incomplete: Celery backed up
SELECT COUNT(*) FROM outbox_event 
WHERE status = 'dispatched' AND dispatched_at < now() - interval '5 min';
```

Active Celery tasks:
```bash
sudo -u bcp /opt/bcp/venv/bin/celery -A config inspect active
sudo -u bcp /opt/bcp/venv/bin/celery -A config inspect reserved
```

## Mitigate

### Case 1: Dispatcher process wedged

```bash
sudo systemctl restart bcp-dispatcher
```

Verify it starts:
```bash
journalctl -u bcp-dispatcher --since "1 min ago" | grep "advisory lock acquired"
```

### Case 2: Advisory lock orphaned

Find the dead PID holding the lock:
```sql
SELECT pid FROM pg_locks WHERE locktype='advisory' AND objid=<DISPATCHER_LOCK_ID>;
```

Kill it:
```sql
SELECT pg_terminate_backend(<pid>);
```

Then restart dispatcher (per Case 1).

### Case 3: Celery backed up

Dispatcher is fine; workers are. Add capacity:
```bash
sudo systemctl edit bcp-celery-worker
# In override.conf:
#   [Service]
#   ExecStart=
#   ExecStart=/opt/bcp/venv/bin/celery -A config worker -l info --concurrency=16
sudo systemctl restart bcp-celery-worker
```

Monitor: `outbox_dispatch_lag_seconds` should decrease within 5 minutes.

### Case 4: Poison message blocking the loop

A specific event type causing the dispatcher to hang (e.g., infinite loop in handler):
```sql
-- Find what's been dispatched but not processed
SELECT event_type, COUNT(*), MIN(dispatched_at)
FROM outbox_event 
WHERE status = 'dispatched' AND dispatched_at < now() - interval '5 min'
GROUP BY event_type
ORDER BY COUNT(*) DESC;
```

If one type dominates, disable its handler (feature flag) and force the rows to DLQ:
```bash
sudo -u bcp /opt/bcp/venv/bin/python /opt/bcp/django/manage.py force_to_dlq \
  --event-type=<EventType> \
  --status=dispatched \
  --reason="<incident-id>: poison events blocking dispatcher"
```

## Resolve

Root causes to investigate:
- Dispatcher OOM (check memory limits + `journalctl -k | grep oom`)
- DB connection pool exhausted (check `pg_stat_activity` count)
- A specific event handler hanging (find which gRPC RPC is slow — Tempo traces)
- Network partition between dispatcher and Redis

## Post-incident

- If recurring: invest in dispatcher resilience
  - Per-event-type isolation (one bad type doesn't starve others)
  - Worker pool per priority
  - Dead-handler timeout enforcement
- Measure: how long was the lag, how many events delayed, did any cross critical thresholds (e.g., live gift broadcast > 30s = bad)
- If poison-message: add test case + idempotency check + handler-level timeout

## Dashboards
- Grafana → Outbox dashboard
- Loki: `{service="bcp-dispatcher"}`
- Tempo: filter by `event_type` for slow handlers
