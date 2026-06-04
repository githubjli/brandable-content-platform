# Diff from Legacy — Mobile Cutover Checklist

This is the **mobile team's playbook** for switching from the legacy backend to the new platform. Use it as a literal checklist; tick each item before cutover.

**Cutover scope**: V1 mobile-only. V2/V3 features stay on legacy until those platform versions ship.

---

## 1. Required changes (breaking)

These changes MUST be in the mobile build that hits the new backend on cutover day.

### 1.1 Auth response shape

**Login response** changes:

| Before | After |
|---|---|
| `{access, refresh, daily_login_reward: {...}}` | `{user, tokens: {access, refresh, expires_at}, session: {id, device_label}}` |

⚠️ Mobile must:
- [ ] Update login response parser
- [ ] Move `access` / `refresh` to `tokens.access` / `tokens.refresh`
- [ ] Remove dependency on `daily_login_reward` being in login response
- [ ] Add explicit `POST /api/v1/economy/daily-rewards/claim` after login (or accept async grant; see below)

**Register response** changes: same shape as login.

**Me response** changes:

| Before | After |
|---|---|
| Flat fields incl. `linked_wallet_id`, `primary_user_address`, `wallet_link_status`, `linked_at` | Removed |
| Top-level User fields | Same minus removed ones, plus `kyc_status` |
| No `creator_profile` | If `is_creator=true`, includes nested `creator_profile` |

### 1.1b MP becomes earned-only

🚫 **All MP purchase endpoints removed.** MP is now purely earned (daily reward, gifts received, creator activity, admin grant, refund). Endpoints that disappear:

| Legacy | Status |
|---|---|
| `GET /api/meow-points/packages/` | removed |
| `POST /api/meow-points/orders/` | removed |
| `GET /api/meow-points/orders/{...}` | removed |
| `POST /api/meow-points/orders/{...}/tx-hint/` | removed |

⚠️ Mobile must:
- [ ] Hide / remove the "Buy MP packages" screen
- [ ] Hide the "purchase" entry point on MeowPoints page
- [ ] Keep `POST /api/v1/economy/daily-rewards/claim` (still works for earning MP)

MP balance can only grow via:
- Daily reward (explicit claim or async-on-login grant)
- Gifts received from other users
- Creator activity rewards (V2+ programs)
- Admin grants
- Refunds (rare)

### 1.2 Daily login reward

**Before**: reward came back in login response automatically.
**After**: two options for mobile:

**Option A** (recommended): call explicit claim after login:
```
POST /api/v1/economy/daily-rewards/claim
→ {granted: true, amount: "10.0000", currency: "MP"}
```

**Option B**: rely on background grant (Django emits Outbox event on login → handler grants → wallet eventually updated). Mobile refreshes wallet balance after login.

- [ ] Mobile: choose A or B
- [ ] Update Profile login flow accordingly
- [ ] Add `GET /api/v1/economy/daily-rewards/status` for next-eligibility UI

### 1.3 Pagination — all list endpoints

**Before**: `?page=N&page_size=N` returning `{count, next, previous, results}`
**After**: `?cursor=<opaque>&limit=N` returning `{results, cursor: {next, prev}}`

⚠️ Mobile must update **every list endpoint** caller:
- [ ] Replace pagination state model (page → cursor)
- [ ] Update list loading patterns
- [ ] Handle `cursor.next == null` as end-of-list

Affected endpoints (non-exhaustive):
- `/api/v1/economy/wallets/me/*/ledger`
- `/api/v1/account/library/*`
- `/api/v1/content/video/public`
- `/api/v1/content/drama/series`
- `/api/v1/content/live/streams`
- `/api/v1/commerce/shop/products`
- `/api/v1/commerce/cart`
- `/api/v1/commerce/orders`
- All comments endpoints

### 1.4 Error envelope

**Before**: `{"detail": "..."}` or `{"code": "...", "detail": "..."}` or `{"field": ["..."]}`
**After**: `{"error": {"code": "...", "message": "...", "detail": {...}}}`

- [ ] Update error parser
- [ ] Update i18n message keys (now keyed by `code`, not `message`)
- [ ] Handle structured `detail` for richer UX (e.g., `WALLET_INSUFFICIENT_BALANCE` returns `{required, available, currency}`)

### 1.5 Money fields

**Before**: mixed — wallet returns string `"1234.56"`, UserBalance returns integer
**After**: always string Decimal with 4 places `"1234.5678"`, always paired with `currency`

- [ ] Update parsers to expect string Decimal
- [ ] Use proper Decimal lib (e.g., `decimal.Decimal` in Dart) — no float math
- [ ] Update display formatting (currency code + locale-aware decimal separator)

### 1.6 Aggregate balance endpoint

**Before**: `GET /api/user-balance/balance/` → `{meow_points: {balance: 1234, currency: "MP"}, coins: 1234, currency: "MP"}`
**After**: `GET /api/v1/economy/wallets/me` → `{balances: [{currency: "MP", amount: "1234.0000"}, {currency: "MC", amount: "56.7800"}]}`

- [ ] Update path
- [ ] Update parser (array instead of object)
- [ ] Remove `coins` / top-level `currency` references

### 1.7 Follow / Subscribe

✅ Mobile already uses `/api/public/users/{id}/follow/`. New platform keeps this. **No change needed.**

But — mobile likely still has dead code for legacy paths. Recommended cleanup:
- [ ] Remove any `/api/creators/{id}/follow/` calls
- [ ] Remove `subscriber_count` / `is_subscribed` parsing (use `follower_count` / `is_following`)

### 1.8 User-context fields

**Before**: flat fields like `is_liked`, `viewer_is_following`, `can_watch`, `is_subscribed`
**After**: nested under `viewer_context`:
```json
{
  "viewer_context": {
    "is_liked": false,
    "is_following": false,
    "can_watch": true
  }
}
```

- [ ] Update all video / drama / user response parsers

### 1.9 Owner fields

**Before**: flat `owner_id`, `owner_name`, `owner_avatar_url`, `owner_is_creator`
**After**: nested under `owner`:
```json
{
  "owner": {
    "id": "<uuid>",
    "display_name": "...",
    "avatar_url": "...",
    "is_creator": true
  }
}
```

- [ ] Update video / drama / live parsers

### 1.10 Counts fields

**Before**: flat `like_count`, `comment_count`, `view_count`, `share_count`, `gift_count`, `gift_points_total`, `gift_amount_total`
**After**: nested under `counts`:
```json
{
  "counts": {
    "view": 12345,
    "like": 678,
    "comment": 42,
    "share": 12,
    "gift_amount": "100.0000",
    "gift_currency": "MP"
  }
}
```

- [ ] Update parsers

### 1.11 Shipping addresses

**Before**: two endpoints with different field names
- `/api/account/shipping-addresses/` with `full_name`, `street_address`
- `/api/shipping-addresses/` with `name`, `address`

**After**: single `/api/v1/commerce/shipping-addresses` with `recipient_name`, `street_address`, etc.

- [ ] Update path (use new)
- [ ] Update field names: `name` → `recipient_name`, `address` → `street_address`

### 1.12 KYC structure

**Before**: documents as keyed object `{id_front: {...}, selfie: {...}}`
**After**: same (kept for mobile compatibility), but PUT replaces POST:

- [ ] Use `PUT /api/v1/account/kyc` instead of `POST /api/v1/account/kyc/`
- [ ] Document upload path stays POST

### 1.13 Identifier types

**Before**: mostly integer IDs (e.g., `video.id: 123`)
**After**: UUIDs everywhere (`video.id: "550e8400-..."`)

- [ ] Update all model parsers
- [ ] Update route patterns (still works because URL parsing is generic)
- [ ] Update local DB schema (Drift) to use string PKs

### 1.14 Drama unlock currency

**Before**: response had `points_charged` and `credits_charged` as separate fields (one zero, one non-zero)
**After**:
```json
{
  "amount": "10.0000",
  "currency": "MP"
}
```

- [ ] Update unlock response parser
- [ ] Logic now: amount > 0, currency tells which wallet

### 1.15 Gift send shape

**Before**:
```json
POST /api/live/{id}/gifts/send
Request: {amount: 100, payment_method: "meow_points"}
Response: {transaction: {...}, sender_balance: 5000, ...}
```

**After**:
```json
POST /api/v1/content/live/streams/{stream_id}/gifts/send
Idempotency-Key: <client uuid>
Request: {amount: "100.0000", currency: "MP", payment_method: "meow_points", gift_code: "rose"}
Response: {transaction: {...}, sender_balance: {currency, amount}, receiver_balance: {currency, amount}, event: {...}}
```

- [ ] Update request: amount as string, add `currency`, add `gift_code` (optional)
- [ ] Send `Idempotency-Key` header
- [ ] Update response parser: balance objects nested

### 1.16 Stripe support

**New**: payment_provider parameter on order creation supports `"stripe"`.

If mobile accepts USD payments via Stripe:
- [ ] Integrate Stripe SDK (per-platform)
- [ ] Handle `client_secret` from order response → present to SDK
- [ ] Handle Stripe SDK confirmation result

If mobile stays MeowPoints/Credit-only:
- [ ] No change

---

## 2. Backward-compatible additions (optional adoption)

These are new features mobile **can** adopt but doesn't need on cutover day.

### 2.1 Logout & sessions
- New `POST /api/v1/auth/logout` and `GET /api/v1/auth/sessions` for force-logout UI

### 2.2 Email verification & password reset
- New `POST /api/v1/auth/password/reset/request` and `confirm` flows

### 2.3 Idempotency-Key header
- Optional on most endpoints; **required on money-touching** (gift, order, unlock)
- Mobile should adopt for retry safety on poor networks

### 2.4 X-Trace-Id propagation
- Optional; if mobile sends, it appears in server logs for debugging

### 2.5 Platform config
- New `GET /api/v1/platform/config` at app launch for branding / feature flags / Stripe key

---

## 3. Cutover-day operational changes

### 3.1 Endpoint base path

⚠️ Almost all paths gain `/v1`:
- `/api/auth/...` → `/api/v1/auth/...`
- `/api/account/...` → `/api/v1/account/...`
- etc.

- [ ] Update API client base URL or path prefix

### 3.2 WebSocket endpoint

Live chat:
- **Before**: `wss://django-backend/ws/live/<id>/chat/`
- **After**: `wss://live-runtime.example.com/ws/v1/live/<id>/chat`

⚠️ V3 only — until Live Runtime ships, mobile stays on legacy backend for live.

### 3.3 New domain endpoints

- DM chat: new ChatService gateway WebSocket
- Notifications: server-pushed (not yet — V2)

---

## 4. Web frontend cleanup (pre-cutover blocker)

⚠️ **Web is currently on legacy follow endpoint**:
- Web uses `POST /api/channels/{owner_id}/subscribe/`
- New platform does NOT implement this path

**Required action before cutover** (any cutover, even mobile-only):
- [ ] Web team migrates to `POST /api/v1/public/users/{user_id}/follow`
- [ ] Web cutover NOT needed; web can keep using legacy backend
- [ ] BUT: legacy backend must continue to expose the old subscribe path for web in parallel

Two options:
- Option A: legacy backend keeps old path indefinitely (until web migrates)
- Option B: web migrates to new path even while pointing at legacy backend (path can be supported in legacy too)

Recommended: Option B + maintain legacy path as deprecated alias in legacy backend.

---

## 5. Things mobile should NOT change

These work the same — leave them alone:
- Auth header format (`Authorization: Bearer <jwt>`)
- KYC documents upload (multipart, document_type keyed)
- Avatar upload (multipart)
- WebSocket frame shape for live chat (matches new Runtime)
- Cart semantics (add/remove/count)
- Order status enum values (`pending_payment`, `paid`, `shipping`, `completed`, `settled`, `cancelled`)
- KYC status enum values
- Drama unlock status (`is_unlocked`, `can_watch`)

---

## 6. Rollback safety

If cutover encounters issues, mobile must support **both** backends temporarily:
- Feature flag `use_new_backend` in client config
- Default false (legacy)
- Flip to true once new backend stable
- Per-domain flags for finer-grained rollback (e.g., `new_backend_wallet`, `new_backend_orders`)

Operations team can re-flip if issues emerge.

---

## 7. Versioned testing matrix

Before cutover, run mobile against new backend with:

| Test | Status | Notes |
|---|---|---|
| Register → login → me | ⬜ | |
| Login → wallet balance shows daily reward | ⬜ | Async grant may take seconds |
| Daily reward explicit claim | ⬜ | |
| KYC submit → upload → submit | ⬜ | |
| Shop browse → cart add → cart count | ⬜ | |
| Order create (Stripe) → pay → see order | ⬜ | |
| Order create (MP) → instant pay → see order | ⬜ | |
| Drama browse → unlock (MP) → watch | ⬜ | |
| Drama unlock with insufficient balance returns proper error | ⬜ | |
| Video like → unlike → like count consistent | ⬜ | |
| Send video gift → balance changes | ⬜ | |
| Send drama gift → balance changes | ⬜ | |
| Library: history shows recently watched | ⬜ | |
| Library: purchased shows new unlock | ⬜ | |
| Library: gifts sent shows new gift | ⬜ | |
| Logout → tokens invalid | ⬜ | |
| Refresh token rotation | ⬜ | |
| Force logout via /sessions/{id} DELETE | ⬜ | |

V3 (post-cutover):
- Live stream watch (WebRTC + HLS fallback)
- Live chat WebSocket
- Live gift broadcast received
- Live broadcaster start/end flow

---

## 8. Mobile dependency checklist

- [ ] Update API client SDK to v1 paths
- [ ] Update model classes (UUID, nested objects, cursor pagination)
- [ ] Update error parser (envelope)
- [ ] Update money parser (Decimal string)
- [ ] Update Riverpod / Bloc providers as needed
- [ ] Add Idempotency-Key generation utility
- [ ] Add OpenTelemetry / trace-id forwarding (optional but recommended)
- [ ] Update Stripe integration (if accepting USD)
- [ ] Update feature flag handling

---

## 9. Acceptance criteria for cutover

Cutover approved when:
- [ ] All §1 breaking changes shipped in mobile production build
- [ ] All §7 test matrix items pass
- [ ] Backend feature-parity validation per `MOBILE_API_CONTRACT_FULL.md` §45 V1 must-have list complete
- [ ] Web `/api/channels/` migration complete (or legacy backend supports both paths)
- [ ] Migration plan dry-runs successful (per `docs/migration-plan.md`)
- [ ] Rollback procedure tested
- [ ] On-call rotation prepared

---

## 10. Open mobile questions

If unclear, ask backend team before cutover:
- Final list of `Idempotency-Key`-required endpoints
- Stripe Connect timing (does mobile need it for V1?)
- Push notification timing (V2 confirmed?)
- Drama membership unlock UX (auto vs explicit?)
