# gRPC Integration

How Django talks to the three gRPC services without coupling itself to them operationally.

> See `contracts/conventions.md` for proto conventions, error envelope, and auth headers. This document is the **client-side integration** story.

---

## 1. Call site rule

gRPC clients are called **only from `services.py`**, never from `views.py` or `serializers.py`. Views call services; services may call gRPC. This keeps HTTP and gRPC boundaries on separate layers and lets us mock gRPC in unit tests cleanly.

**Enforcement**: import-linter rule:
```
[importlinter:contract:grpc-call-site]
name = gRPC clients only callable from services.py
type = forbidden
source_modules = django.apps.*.views, django.apps.*.serializers
forbidden_modules = libs.grpc_client.*
```

---

## 2. Sync vs async decision

| Scenario | Pattern |
|---|---|
| User-visible work that needs the result inside the HTTP request | Sync gRPC call from service, short deadline (≤ 3s) |
| Side effect that user doesn't wait on (notifications, broadcasts, analytics) | Write `OutboxEvent` → dispatcher → Celery handler → gRPC call |
| Anything money-touching | The money write stays in Django + atomic; the side effect goes Outbox |

**Default to Outbox.** Sync gRPC inside a request requires a written justification in the service docstring.

---

## 3. Deadlines (mandatory)

Every RPC has a deadline. No exceptions.

```python
response = notification_client.Send(request, timeout=3.0)
```

Defaults (override in code with reason):

| Call type | Default deadline |
|---|---|
| Sync user-path RPC (e.g., get watch config) | 3 seconds |
| Background Outbox-driven RPC | 30 seconds |
| Streaming RPC (per-message) | 30 seconds |
| Streaming RPC (total session) | unbounded but with keepalive |

A deadline expiry returns `DEADLINE_EXCEEDED`. The caller treats it as a retriable failure (per §4 retry policy).

---

## 4. Retries

Only **idempotent** RPCs retry. Idempotency is declared in proto:
```proto
rpc SendNotification(SendRequest) returns (SendResponse) {
  option (idempotency.level) = IDEMPOTENT;
}
```

Retry policy:
- Max 3 attempts
- Exponential backoff: 100ms, 500ms, 2s (with jitter)
- Retry on: `UNAVAILABLE`, `DEADLINE_EXCEEDED`, `INTERNAL` (last one carefully)
- Never retry on: `INVALID_ARGUMENT`, `PERMISSION_DENIED`, `NOT_FOUND`, `ALREADY_EXISTS`

Non-idempotent RPCs (rare) require explicit caller-side idempotency keys passed in the request.

---

## 5. Circuit breaker

Each `(service, RPC)` pair has a circuit breaker (in `libs/grpc_client/circuit_breaker.py`):
- After 5 consecutive failures, open the circuit
- While open, calls fail fast with custom `CIRCUIT_OPEN` exception (not a real gRPC status)
- After 30 seconds, half-open: allow one probe
- Probe succeeds → close. Probe fails → back to open with extended timeout (exponential to cap of 5 min)

Metrics:
- `grpc_circuit_state{service,rpc}` gauge (0=closed, 1=open, 2=half-open)
- `grpc_circuit_trips_total{service,rpc}` counter

Open circuit triggers a warning alert (not pager) — degraded mode is acceptable, but persistent state means something's wrong.

---

## 6. Degradation (the money rule)

When a gRPC service is unreachable, the system **must not block the user**:

| Scenario | Degradation |
|---|---|
| NotificationService down during registration | Registration succeeds (Django commits + Outbox row); welcome email retries via Outbox dispatcher |
| ChatService down during DM send | Return 503 with explicit message; user retries manually |
| LiveRuntime down during gift send | **Reject the gift up front** (do not debit). Money work is sacred. |
| Watch-config call fails | Return cached config (Redis, 60s TTL) if available; otherwise 503 with degraded message |

The money rule: **a gRPC failure must never leave a wallet in an unknown state.** Either the debit committed and the side effect is in Outbox for retry, or the debit didn't happen at all.

---

## 7. Auth on every call

The gRPC client wrapper (`libs/grpc_client/auth.py`) auto-injects:

```
metadata:
  authorization:  Bearer <jwt>
  x-trace-id:     <current trace_id>
  x-request-id:   <current request_id>
```

The `<jwt>`:
- For user-path RPCs: **pass-through user JWT** (the same one the user sent to Django)
- For background/Outbox RPCs: **service account JWT** (per ADR-0005)

Application code does not handle metadata manually. If you find yourself adding `metadata=` kwargs, you're using the wrong wrapper.

---

## 8. Client code pattern (canonical)

```python
# apps/identity/services.py
from libs.grpc_client.notification import notification_client
from libs.errors import ServiceUnavailable

def register_user(email: str, password: str) -> User:
    with transaction.atomic():
        user = User.objects.create_user(email=email, password=password)
        PointWallet.objects.create(user=user)
        CreditWallet.objects.create(user=user)
        
        OutboxEvent.objects.create(
            event_type="identity.UserRegistered",
            idempotency_key=f"user_registered:{user.id}",
            payload={"user_id": str(user.id), "email": user.email},
        )
        record_audit(action="identity.user.register", ...)
    
    # Notification is async via Outbox; do not call NotificationService directly here.
    return user
```

Then in the Outbox handler:
```python
# apps/identity/handlers.py
from libs.grpc_client.notification import notification_client

@on_event("identity.UserRegistered")
def send_welcome_email(event):
    payload = event.payload
    try:
        notification_client.send(
            idempotency_key=f"welcome:{event.id}",
            template_code="welcome",
            recipient={"user_id": payload["user_id"]},
        )
    except CircuitOpen:
        # Will retry via Outbox dispatcher with backoff
        raise
```

Notice: registration code does not import any gRPC. The handler does.

---

## 9. Local development

When a gRPC service is not running locally:
- The wrapper detects connection refused
- Returns a `DEV_MODE_NOOP` result for known idempotent RPCs
- A WARNING log is emitted (visible but non-fatal)
- Outbox events accumulate as `pending` (dispatcher just doesn't deliver). They process on next service start.

Production: connection refused is a hard failure with no noop — the circuit breaker handles it.

---

## 10. Testing strategy

| Layer | Approach |
|---|---|
| Unit tests | Mock the gRPC client at the boundary (`mock.patch('libs.grpc_client.notification.notification_client')`) |
| Contract tests | Generated mock server from proto; verify request shape on every change |
| Integration tests | Real services started via docker-compose for end-to-end smoke tests |
| Failure injection | Mock client raises specific gRPC status codes; assert proper degradation |

Detail in `ops/testing-strategy.md`.

---

## 11. Discovery

V1: addresses are config:
```
NOTIFICATION_SERVICE_ADDR=localhost:50051
CHAT_SERVICE_ADDR=localhost:50052  # V2
LIVE_RUNTIME_ADDR=localhost:50053   # V3
```

Read from `/etc/bcp/django.env` (per `ops/environments.md`).

V2/V3: consider DNS-based discovery if running multi-host, but a static config file is sufficient at the current scale.

---

## 12. Anti-patterns

- ❌ Calling gRPC from a Django view directly
- ❌ Calling gRPC inside a `transaction.atomic()` block (don't tie commit to network call)
- ❌ Skipping `timeout=` on a gRPC call
- ❌ Retrying non-idempotent RPCs
- ❌ Catching `grpc.RpcError` broadly to swallow failures
- ❌ Building metadata dicts by hand instead of using the auth wrapper
- ❌ Calling gRPC services from Celery tasks without trace propagation (use the wrapper)
