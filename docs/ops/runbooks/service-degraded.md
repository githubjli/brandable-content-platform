# Runbook: gRPC Service Degraded or Down

Covers: bcp-notification (V1), bcp-chat (V2), bcp-live-runtime (V3).

## Symptom
- Error rate spike on a specific gRPC service
- Health check failing
- Latency p99 spike
- Circuit breakers opening (`grpc_circuit_state{service=...} = 1`)

## Severity
- Service down: **page on-call**
- Service degraded (error rate > 5%): **page on-call**
- Live Runtime degraded during active streams: **immediate critical** (revenue + UX)

## Investigate

```bash
SVC=<bcp-notification|bcp-chat|bcp-live-runtime>

systemctl status $SVC
journalctl -u $SVC --since "10 min ago" -n 200
```

Check:
- Recent restarts? `systemctl show $SVC | grep ActiveEnterTimestamp`
- OOM-killed? `journalctl -k | grep oom | tail`
- Recent deploy? `git log --oneline -5`

### Service-specific quick checks

**Notification**:
- Provider health: SendGrid / Twilio / FCM status pages
- Connection to PostgreSQL `bcp_notification` DB
- Outbox-side: how many pending notifications? (could be just upstream demand)

**Chat**:
- Active WebSocket connections: `chat_active_streams` metric
- Memory: chat holds connection state in-process; long-lived
- Redis presence keys: `chat:presence:*`

**Live Runtime**:
- Ant Media connectivity: `curl http://ant-media-server:5080/rest/v2/version`
- Active streams: `live_active_streams`
- Redis: `live:viewers:*` keys

## Mitigate

### Default action: roll back if there was a recent deploy

```bash
ansible-playbook ops/ansible/deploy.yml \
  --extra-vars "env=production branch=<prev-sha>"
```

This restarts the service at the previous commit. Most service issues are deploy-related.

### If not deploy-related: scale horizontally (V2+)

V1 is single-host; can only restart:
```bash
sudo systemctl restart $SVC
```

V2+ on k8s or multi-host:
```bash
kubectl scale deployment/$SVC --replicas=4
```

### If the downstream is the cause

For Notification, if SendGrid is down: events queue in Outbox; resolve when SendGrid recovers. No mitigation possible from our side.

For Live Runtime, if Ant Media is down: live gift sends should already reject up front (per `contracts/content-live.md`). Verify:
```bash
# Spot-check the rejection
curl -X POST https://api.bcp.example.com/api/v1/content/live/streams/<id>/gifts/send \
  -H "Authorization: Bearer <test-jwt>" -d '{...}'
# Should return 502 with code LIVE_RUNTIME_UNAVAILABLE
```

If reject is not happening: deploy hotfix to the gift handler.

## Resolve

The platform is designed to degrade gracefully (per ADR-0006):
- Notification down → Outbox retries; no user-visible failure for non-critical notifications
- Chat down → users get explicit 503 "service temporarily unavailable"
- Live Runtime down → gifts rejected up front (money is sacred)

If degradation is not graceful, that's a design bug. File an issue.

Other resolutions:
- OOM: increase memory limit in systemd unit, restart
- DB connection exhaustion: increase pool size, restart
- Specific handler regression: revert + redeploy
- External provider degraded: notify support, monitor recovery

## Verification

After mitigation:
```bash
# Service up?
systemctl is-active $SVC

# Healthy?
grpcurl -d '{}' -H "authorization: Bearer <internal-token>" \
  localhost:50051 notification.v1.NotificationService/Ping
# Expect: { version: "...", server_time: "..." }

# Error rate dropping?
# Grafana → service-specific dashboard → grpc_requests_total{status="error"} rate
```

## Post-incident

For every gRPC service incident:
- Did the circuit breaker open and protect callers? If not, why?
- Did Outbox retries cover side effects? If not, why?
- Did monitoring catch it before users did? If not, what alert is missing?
- Are the runbooks accurate?
- What's the change to prevent recurrence?

Document in `docs/postmortems/<date>-<service>-<short-desc>.md`.

## Dashboards
- Grafana → service-specific (notification.json / chat.json / live-runtime.json)
- Loki: `{service="$SVC"}`
- Tempo: traces filtered by service
