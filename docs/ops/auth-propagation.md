# Authentication Propagation

JWT (RS256) is the unit of identity across the Django monolith and gRPC services. Identity owns the private key; everyone else verifies via JWKS. Per ADR-0005.

---

## 1. Token shape

```
Header:  { "alg": "RS256", "kid": "<key-id>" }
Payload: {
  "iss": "brandable-content-platform",
  "sub": "<user-uuid>",
  "iat": ...,
  "exp": ...,
  "jti": "<token-uuid>",
  "type": "access" | "refresh" | "service",
  "scope": [...],
  "aud": "<service-identifier>"   # reserved for future multi-brand
}
```

Access tokens are short-lived (15 min). Refresh tokens rotate on use.

---

## 2. Propagation map

```
Browser / mobile  ──(HTTPS)──►  Django (via nginx)
   Header:  Authorization: Bearer <user_access_jwt>
            X-Trace-Id:    <client-or-edge-generated>


Django  ──(gRPC)──►  gRPC service (Notification / Chat / Live Runtime)
   Metadata:
     authorization:  Bearer <user_access_jwt>     ← user-path RPC
     x-trace-id:     <propagated>
     x-request-id:   <propagated>

   For background work (Outbox dispatcher → handler → gRPC):
     authorization:  Bearer <service_account_jwt> ← service-to-service
     x-trace-id:     <propagated from original event>
     x-request-id:   <propagated>
     x-on-behalf-of: <user-uuid>  (optional, for audit)


gRPC service  ──(gRPC)──►  another gRPC service (rare in V1)
   Metadata:
     authorization:  Bearer <service_account_jwt>
     x-trace-id:     <propagated>
     x-on-behalf-of: <user-uuid>   (optional)
```

---

## 3. Rules

- Services **never** reuse the end user's token to call other services. Use a service account JWT (`type=service`).
- Each service has its own service account credentials, scoped to the RPCs it actually calls.
- gRPC interceptor on every service handles: verify signature, check `exp`, check `aud`, inject `User` and `Trace` into context.
- Service accounts cannot mint user tokens; only Identity can.

---

## 4. Key distribution

- Identity exposes JWKS at `https://identity.bcp.example.com/.well-known/jwks.json`
- Each service caches the JWKS for 10 minutes, refreshes on cache miss
- Key rotation: publish new `kid` → services pick up on next refresh → stop signing with old `kid` → remove old key after `exp` window

Detailed rotation procedure: `runbooks/jwt-key-rotation.md`.

---

## 5. Failure modes

| Failure | Service response |
|---|---|
| Missing token | `UNAUTHENTICATED` (gRPC) / 401 (HTTP) |
| Invalid signature | `UNAUTHENTICATED` |
| Expired | `UNAUTHENTICATED` with detail `TOKEN_EXPIRED` |
| Insufficient scope | `PERMISSION_DENIED` |
| Wrong audience | `PERMISSION_DENIED` |
| JWKS endpoint unreachable | Use cached keyset (10 min) + log warning; if cache also empty, refuse all auth |

---

## 6. Service-to-service authentication (canonical pattern)

```python
# libs/grpc_client/notification.py
import grpc
from libs.jwt_auth import get_service_account_token

class NotificationClient:
    def __init__(self):
        self._channel = grpc.insecure_channel(NOTIFICATION_SERVICE_ADDR)
        self._stub = NotificationServiceStub(self._channel)
    
    def send(self, *, idempotency_key, template_code, recipient, context=None):
        # Build metadata
        token = get_service_account_token(service="django", scope=["notification.send"])
        trace_id = current_trace_id()
        request_id = current_request_id()
        metadata = (
            ("authorization", f"Bearer {token}"),
            ("x-trace-id", trace_id),
            ("x-request-id", request_id),
        )
        
        # Call with deadline
        return self._stub.Send(
            SendRequest(
                idempotency_key=idempotency_key,
                template_code=template_code,
                recipient=recipient,
                context=context or {},
                trace_id=trace_id,
            ),
            metadata=metadata,
            timeout=3.0,
        )
```

The application code calling `.send(...)` never touches metadata, tokens, or tracing primitives.

---

## 7. JWT private key storage

- Mounted as a file at `/run/secrets/jwt-private.pem` with mode 0400
- Owner: `bcp-identity` service user
- Loaded once at process start; cached in memory
- Never written to disk by the application
- Never logged

---

## 8. Local development

- Identity service generates a dev keypair on first run via `make dev-keys`
- Other services point at `http://localhost:8000/.well-known/jwks.json`
- The keypair is git-ignored and stored under `ops/dev-keys/`
- For testing service-to-service auth locally, helpers in `tests/utils/auth.py` mint short-lived dev tokens

---

## 9. Token revocation

Three mechanisms:
1. **Natural expiry**: access tokens expire in 15 minutes
2. **Refresh revocation**: deleting `UserSession` invalidates refresh; access continues until expiry
3. **Force-logout all**: bulk delete `UserSession` for a user (admin action; audited as `identity.session.revoke`)

No revocation list (CRL) is maintained — short access TTL is the trade-off.

---

## 10. Anti-patterns

- ❌ Bearer-style HS256 tokens
- ❌ Using user tokens for service-to-service calls
- ❌ Including PII (email, phone) in JWT claims
- ❌ Setting access TTL > 30 minutes
- ❌ Skipping refresh rotation
- ❌ Caching JWKS forever
- ❌ Manually building Bearer headers in application code (use the wrapper)
