# Conventions

Cross-cutting standards every endpoint must follow. **No exceptions without an ADR.**

## 1. Base paths

| Surface | Path |
|---|---|
| REST | `/api/v1/<resources>/` |
| WebSocket | `/ws/v1/...` |
| Health | `/api/v1/health` (no version drift after v2) |
| Metrics | `/internal/metrics` (Prometheus) |
| Admin | `/admin/` (Django admin) |

## 2. Authentication

### User tokens
```
Authorization: Bearer <access_jwt>
```

- **Algorithm**: RS256 (asymmetric)
- **Issuer**: Identity service (Django)
- **Access TTL**: 15 minutes
- **Refresh TTL**: 7 days, rotates on use
- **JWKS endpoint**: `/.well-known/jwks.json` (public keys)

### Token claims
```json
{
  "iss": "brandable-content-platform",
  "sub": "<user-uuid>",
  "iat": 1717488000,
  "exp": 1717488900,
  "jti": "<token-uuid>",
  "type": "access" | "refresh" | "service",
  "scope": ["..."],
  "aud": "<service-or-audience>"
}
```

### Service-to-service
```
Authorization: Bearer <service_account_jwt>
x-on-behalf-of: <user-uuid>   (optional, for audit)
```

Services **never** reuse end-user tokens.

### Session tracking
`UserSession` table tracks active refresh tokens. Deleting a session invalidates the refresh token; access tokens remain valid up to 15 minutes.

## 3. Idempotency

Write endpoints accept `Idempotency-Key` header (max 128 chars). Same key within 24 hours returns the cached response without re-executing.

```
POST /api/v1/economy/wallets/me/debit
Idempotency-Key: 7e8f9a-...
```

**Money-touching endpoints MUST require `Idempotency-Key`.** This is enforced at the service layer.

## 4. Pagination

**Single style: cursor-based.** No `?page=N` anywhere.

```
GET /api/v1/resources/?cursor=<opaque>&limit=20
```

| Param | Type | Default | Max |
|---|---|---|---|
| `cursor` | string (opaque) | none (first page) | — |
| `limit` | integer | 20 | 100 |

Response envelope:
```json
{
  "results": [...],
  "cursor": {
    "next": "<opaque or null>",
    "prev": "<opaque or null>"
  }
}
```

Filtering uses additional query params per endpoint. Pagination cursors encode the filter + sort key.

## 5. Error envelope

All errors use this shape:

```json
{
  "error": {
    "code": "WALLET_INSUFFICIENT_BALANCE",
    "message": "Insufficient balance for this transaction.",
    "detail": {
      "required": "100.00",
      "available": "37.50",
      "currency": "MP"
    }
  }
}
```

### Required fields
- `code`: SCREAMING_SNAKE_CASE, stable across versions. Used for programmatic dispatch.
- `message`: human-readable English. UI may show this or translate by `code`.
- `detail`: optional object with structured context.

### Status code mapping

| HTTP | Use case |
|---|---|
| 200 | Success (idempotent read or write) |
| 201 | Resource created |
| 204 | Success with no body |
| 400 | Validation error |
| 401 | Missing or invalid token |
| 403 | Authenticated but not authorized |
| 404 | Resource not found |
| 409 | Conflict (e.g., duplicate, state mismatch) |
| 422 | Semantically invalid (e.g., business rule violation) |
| 429 | Rate limited |
| 500 | Internal error (server bug) |
| 502 | Upstream dependency failure (e.g., gRPC service, payment provider) |
| 503 | Service temporarily unavailable |

### Error code namespace

Codes are prefixed by domain:
- `AUTH_*` — authentication
- `WALLET_*` — economy
- `ORDER_*` — payments / commerce
- `KYC_*` — KYC
- `LIVE_*` — live
- `RATE_LIMIT_*` — rate limiting
- `VALIDATION_*` — input validation
- `INTERNAL_*` — server-side issues

## 6. Time

- All timestamps ISO 8601 UTC: `2026-06-04T10:23:45.123Z`
- Use the `Z` suffix (not `+00:00`).
- All times are server-authoritative; client timezone is for display only.
- Stored in DB as UTC, serialized as UTC.

## 7. Money

### Format
Money fields use **string decimal** representation to avoid float precision issues:

```json
{
  "amount": "1234.5678",
  "currency": "MP"
}
```

- DB column: `Decimal(18, 4)` — 4 decimal places to support sub-cent precision.
- Wire format: string with at most 4 decimal places, no thousands separators.
- **No `float` anywhere.**

### Currency codes (canonical)

Currency codes are **ticker symbols**. On-chain tokens follow the convention `<TICKER>-<CHAIN>` (e.g., `THB-LTT`, `USDT-ETH`, `USDC-SOL`). Always disambiguate by the (provider, network, currency) tuple — never assume currency alone is sufficient.

| Code | Class | Lives on | Provider context |
|---|---|---|---|
| `MP` | Platform virtual (MeowPoints) | Internal ledger (`apps/economy/`) | `payment_provider=wallet` |
| `MC` | Platform virtual (MeowCredit) | Internal ledger (`apps/economy/`) | `payment_provider=wallet` |
| `USD` | Fiat | Banking rails | `payment_provider=stripe` |
| `LBC` | On-chain native token | LBC (LBRY) chain | `payment_provider=blockchain`, `blockchain_network=lbc` |
| `THB-LTT` | On-chain stablecoin (peg ≈ Thai Baht; analogous to USDT-ETH) | LTT chain | `payment_provider=blockchain`, `blockchain_network=ltt` |
| `USDT-ETH` | On-chain stablecoin (peg USD) — future | Ethereum chain | `payment_provider=blockchain`, `blockchain_network=eth` |
| `USDC-SOL` | On-chain stablecoin (peg USD) — future | Solana chain | `payment_provider=blockchain`, `blockchain_network=sol` |
| `USDT-TRON` | On-chain stablecoin (peg USD) — future | Tron chain | `payment_provider=blockchain`, `blockchain_network=tron` |

### Three layers — never collapse

```
payment_provider:     stripe  │  blockchain  │  wallet  │  manual
                                     │
                                     └─ blockchain_network:    lbc │ ltt │ (future: eth, sol, tron, bsc, ...)
                                                  │
                                                  └─ currency (token on that chain):  LBC │ THB-LTT │ USDT-ETH │ ...

payment_provider=stripe   → currency = fiat (USD, EUR, ...)
payment_provider=wallet   → currency = MP | MC (platform virtual; ALL spending happens here)
payment_provider=manual   → currency = whatever admin records
```

**Mental model**:
- **MP / MC** are like in-app coins — never leave the platform DB, no chain involved. They are the **payment endpoint** for in-platform purchases (drama unlock, gift, etc.).
- **LBC** is special: same name for the chain *and* its native token (like "ETH" on Ethereum).
- **LTT** is a chain; **THB-LTT** is the stablecoin on it (pegged ~ Thai Baht). Same pattern as Ethereum/USDT-ETH.
- Token naming `<TICKER>-<CHAIN>` makes it impossible to mistake `USDT-ETH` for `USDT-TRON` — they're different tokens even though both are "USDT" by ticker.
- Fiat flows through Stripe with simple uppercase ISO codes (`USD`, `EUR`); no `-CHAIN` suffix because fiat has no chain.

**Removed in new platform**: legacy single-string tags like `thb_ltt`, `meow_points`, `meow_credit` as `currency` values. Provider/network/currency are three separate fields; their compositions are explicit.

### Aggregate balance
```json
{
  "balances": [
    {"currency": "MP", "amount": "1234.0000"},
    {"currency": "MC", "amount": "56.7800"}
  ]
}
```

**Removed**: legacy `coins` and top-level `currency` aliases.

## 8. Identifiers

- All entity IDs are **UUID v4** strings (no auto-increment integers).
- `idempotency_key` is a client-generated string (max 128 chars). Suggested format: `<domain>:<entity>:<nanoid>`.
- `order_no` / `redeem_no` are server-generated human-readable strings (e.g., `ORD-2026-0604-001234`).

## 9. Naming

| Style | Used for |
|---|---|
| `snake_case` | JSON field names, URL paths |
| `PascalCase` | Type names in docs |
| `SCREAMING_SNAKE_CASE` | Error codes, enums in proto |

### Suffix conventions
- `_url` — absolute URL strings
- `_at` — timestamps
- `_id` — UUID references
- `_count` — non-negative integers (no negatives)
- `_amount` — money values (always paired with `currency`)

**Removed legacy aliases** (do not implement):
- `channel_urls`, `creator_live_urls`, `channel_url`, `live_url`, `profile_url`, `web_url` — three-frontend confirmed unused
- `coins` (use `MP` balance)
- `subscriber_count` / `is_subscribed` (use `follower_count` / `is_following`)
- `channel_id` / `channel_name` (use `owner_id` / `owner_name`)

## 10. Tracing & Observability

### Trace propagation
```
HTTP request:
  X-Trace-Id: <uuid>          (generated if absent)
  X-Request-Id: <uuid>         (per-request)

gRPC metadata:
  x-trace-id: <propagated>
  x-request-id: <propagated>

Celery task header:
  trace_id: <propagated>
```

### Logging
- Structured JSON to stdout.
- Every log line includes: `timestamp`, `level`, `service`, `trace_id`, `request_id`, `user_id` (if known), `message`.
- No sensitive fields logged: passwords, tokens, full PII.

## 11. Rate limiting

- Authenticated: 600 req/min per user
- Unauthenticated: 60 req/min per IP
- Login endpoint: 10 req/min per IP (anti-bruteforce)
- Returns 429 with `Retry-After` header in seconds.

```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Too many requests.",
    "detail": {"retry_after_seconds": 30}
  }
}
```

## 12. CORS

Allowed origins are configured per environment via PlatformConfig (not env var).

## 13. File uploads

`multipart/form-data` for files. Max upload size:
- Avatar: 5 MB
- KYC document: 10 MB
- Video upload: 2 GB (chunked)
- Thumbnail: 5 MB

Files stored under `/media/` (V1 local FS, V2 S3-compatible object store). Absolute URLs returned in responses use the platform's configured public base URL.

## 14. Service-layer enforcement

These rules are enforced at the service layer (not view layer):

1. All money writes go through `EconomyService.credit/debit` (per ADR-0004).
2. All OutboxEvent writes inside the same DB transaction as the business write.
3. All cross-app calls through `services.py` (no direct cross-app model imports — enforced by `import-linter`).
4. All gRPC client calls have explicit `timeout=` (default 3 seconds for sync paths).

## 15. Breaking change policy

A change is **breaking** if:
- A field is removed or renamed
- A field type changes
- A field becomes required
- An error code changes
- An endpoint is removed without `/api/v2/` replacement

Breaking changes require:
1. ADR explaining why
2. Path versioning (v2 alongside v1)
3. Minimum 1 release of coexistence
4. Sunset announcement in `diff-from-legacy.md`
