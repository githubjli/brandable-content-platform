# Deprecated / Not-Implemented

Endpoints, fields, and patterns from legacy that are **explicitly NOT implemented** in the new platform. This file exists to prevent rediscovery — if you find yourself wondering "should we add X?", check here first.

---

## 1. Endpoints not implemented

### Legacy Channel concept

🚫 **The entire `channel_urls.py` is dropped.** Mobile and web confirm channel is not an independent entity.

| Endpoint | Replaced by |
|---|---|
| `POST /api/channels/{id}/subscribe/` | `POST /api/v1/public/users/{user_id}/follow` |
| `DELETE /api/channels/{id}/subscribe/` | `DELETE /api/v1/public/users/{user_id}/follow` |

⚠️ **Web still uses the legacy path.** Web must migrate to the new path before cutover. Tracked in `diff-from-legacy.md`.

### Legacy creators_urls

| Endpoint | Replaced by |
|---|---|
| `POST /api/creators/{id}/follow/` | `POST /api/v1/public/users/{user_id}/follow` |
| `DELETE /api/creators/{id}/follow/` | `DELETE /api/v1/public/users/{user_id}/follow` |

### Wallet Prototype

| Endpoint | Reason |
|---|---|
| `POST /api/wallet-prototype/pay-order/` | Prototype — required linked wallet + password to sign chain tx. Replaced by `POST /api/v1/payments/orders/{order_no}/verify` |
| `POST /api/wallet-prototype/pay-product-order/` | Same |

### MP purchase endpoints (MP is earned-only)

🚫 **MP cannot be purchased via any channel — neither blockchain nor Stripe.** Per economy.md §4, MP grows only via earned mechanisms (daily reward, gifts received, creator activity, admin grant, refund).

| Endpoint | Reason |
|---|---|
| `GET /api/meow-points/packages/` | No MP packages exist; only credit packages |
| `POST /api/meow-points/orders/` | MP not purchasable |
| `GET /api/meow-points/orders/` | Same |
| `GET /api/meow-points/orders/{order_no}/` | Same |
| `POST /api/meow-points/orders/{order_no}/tx-hint/` | Same |

⚠️ Mobile must remove the "Buy MP packages" UI in the cutover release.

### Login-embedded daily reward

🚫 Legacy `POST /api/auth/login/` response contained `daily_login_reward` field with grant info, computed synchronously.

**Replaced by**: async grant via `OutboxEvent` + explicit `POST /api/v1/economy/daily-rewards/claim`. See identity.md §1 and economy.md §6.

### Hidden side-effect on GET endpoints

🚫 Legacy `GET /api/meow-points/orders/` and `GET /api/meow-credit/recharges/` auto-credited paid orders as a side effect of reading.

**New platform**: GET is read-only. Crediting happens via:
- Stripe webhook (V1)
- `verify-now` endpoint (Blockchain — any network via adapter dispatch)
- Scheduled reconciliation job (catch-all)

---

## 2. Fields not implemented

### Mobile + Web confirmed unused

| Field | Source serializer | Reason |
|---|---|---|
| `channel_urls` | Video, Drama, Live | Zero frontend consumption |
| `creator_live_urls` | Live | Same |
| `live_url` | Live | Same |
| `channel_url` | various | Same |
| `profile_url` | various | Same |
| `web_url` | various | Same |

### Alias fields

| Field | Source | Replaced by |
|---|---|---|
| `coins` | UserBalance aggregate | `balances[].amount` where `currency=MP` |
| top-level `currency` | UserBalance | per-balance `currency` |
| `subscriber_count` | User / Profile / Video / Drama | `follower_count` |
| `viewer_is_subscribed` | various | `viewer_is_following` (in `viewer_context`) |
| `is_subscribed` | various | `is_following` |
| `channel_id` | Video, Drama | `owner_id` |
| `channel_name` | Video, Drama | `owner_name` (within `owner` object) |
| `viewer_is_following` (flat) | various | `viewer_context.is_following` |
| `is_following_owner` (flat) | various | `viewer_context.is_following` |

### Blockchain prototype residue (in User / Me responses)

| Field | Reason |
|---|---|
| `linked_wallet_id` | Prototype wallet linking — replaced by economy app |
| `primary_user_address` | Same |
| `wallet_link_status` | Same |
| `linked_at` | Same |

### Snapshot fields with weak semantics

| Field | Source | Replaced by |
|---|---|---|
| `total_points` | GiftTransaction | `amount` + `currency` |
| `gift_name_snapshot` | GiftTransaction | `gift_code` (display hint only) |
| `points_price_snapshot` | GiftTransaction | `amount` + `currency` |
| `quantity` | GiftTransaction | always 1 (fixed-gift mode dropped) |
| `gift_id` | GiftTransaction | dropped (amount-only) |

---

## 3. Patterns not implemented

### Three pagination styles

🚫 Legacy used:
- `?page=N` + `?page_size=N` (most endpoints)
- `?after_id=N` + `?limit=N` (live chat)
- `?limit=N` + `?offset=N` (some video comments)

**New platform**: **cursor only** (`?cursor=<opaque>&limit=<n>`). See conventions.md §4.

### Three error response styles

🚫 Legacy returned:
- `{"detail": "..."}` (DRF default)
- `{"field": ["error"]}` (DRF field errors)
- `{"code": "...", "detail": "..."}` (some business errors)

**New platform**: single envelope `{"error": {"code", "message", "detail"}}`. See conventions.md §5.

### Two shipping-address endpoints

🚫 Legacy had:
- `/api/account/shipping-addresses/` with `full_name`, `street_address`, etc.
- `/api/shipping-addresses/` with `name`, `address`, etc.

**New platform**: single endpoint `/api/v1/commerce/shipping-addresses` with unified naming (`recipient_name`, `street_address`). See commerce.md §10.

### Two shop-category endpoints

🚫 Legacy had:
- `/api/public/categories/` (general)
- `/api/shop/categories/` (with synthetic "All")

**New platform**: `/api/v1/commerce/shop/categories` includes the "All" pseudo-category; `/api/v1/public/categories` is the source.

### Live-only fixed gifts

🚫 Legacy `LiveGiftSendAPIView` supported both:
- Amount mode (preferred): `{amount, payment_method}`
- Fixed mode (legacy): `{gift_id, quantity}` with 2-second dedup window

**New platform**: amount mode only. `gift_code` is a display hint, not a charge calculator. See gift.md §2.

### Lazy wallet creation

🚫 Legacy created `PointWallet` and `CreditWallet` on first access, logging a warning. Silent surprise behavior.

**New platform**: wallets created **explicitly at user registration**. See identity.md §1.

### Login-side reward grant (no audit trail)

🚫 Legacy `POST /api/auth/login/` called `MeowPointService.grant_daily_login_reward(user)` synchronously. Failures swallowed.

**New platform**: async via Outbox; failures land in DLQ with alerts.

---

## 4. Features explicitly deferred

Things mobile / legacy has but new platform won't implement in V1-V3 without product re-prioritization:

| Feature | Reason |
|---|---|
| Real video transcoding (ffmpeg / cloud transcoder) | Legacy doesn't have it either; mobile works without |
| CDN for video / image delivery | V3+; local FS in V1, S3 in V2 |
| OAuth / social login (Google, Facebook, Line) | Not in mobile usage; revisit if business need emerges |
| Push notifications (FCM / APNs) | Mobile has zero push infrastructure; defer to V2 |
| Streaming push notifications (in-app feed) | Not a mobile-confirmed feature |
| Drama recommendations engine | V3+ |
| Search (full-text across content) | V3+ |
| Comment moderation tools (admin) | V3+ |
| Stream recording / VOD | V3+ |
| Co-streaming / guests | V3+ |
| User-initiated refund via Stripe | V2 (Blockchain manual refund is V1) |
| Stripe Connect (seller payouts) | V3 |
| Subscription tiers with upgrade/downgrade | V3 |
| Free-trial subscriptions | V3 |

---

## 5. Things to add to this file when discovered

Any time during implementation, when someone proposes "let's add X back from legacy", check this file. If X is listed → don't. If not listed but you decide not to do it → add it here.

This file is the institutional memory of "intentionally dropped." Without it, every quarter someone reintroduces `channel_id`.

---

## 6. Quick reference table

| Legacy artifact | Status | Where |
|---|---|---|
| `channel_urls.py` | 🚫 Dropped | this file |
| `wallet_prototype_urls.py` | 🚫 Dropped | this file |
| `creators_urls.py` follow/unfollow | 🚫 Dropped | this file |
| `?page=N` pagination | 🚫 Replaced by cursor | conventions.md §4 |
| `{detail: "..."}` errors | 🚫 Replaced by envelope | conventions.md §5 |
| `daily_login_reward` in login response | 🚫 Replaced by async | identity.md §1 |
| Lazy wallet creation | 🚫 Replaced by explicit | identity.md §1 |
| Fixed-gift mode | 🚫 Replaced by amount mode | gift.md §2 |
| `linked_wallet_id` family | 🚫 Removed | identity.md §1 |
| `coins`, top-level `currency` | 🚫 Removed | economy.md §1 |
| `subscriber_*`, `channel_*` aliases | 🚫 Removed | conventions.md §9 |
| Two shipping-address endpoints | 🚫 Consolidated | commerce.md §10 |
| Hidden side-effect on GET | 🚫 Read-only GETs | this file |
| Real video transcoding | ⏳ Deferred V3 | this file |
| Push notifications | ⏳ Deferred V2 | this file |
