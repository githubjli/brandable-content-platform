# LEGACY SYSTEM SNAPSHOT — DO NOT EDIT

> 📸 **Snapshot of `django-auth-core/MOBILE_API_CONTRACT_FULL.md` taken on 2026-06-04.**
>
> **This file is immutable.** Use it as historical reference during migration:
> - Migration planning (`migration/migration-plan.md`)
> - Cutover delta (`contracts/diff-from-legacy.md`)
> - "What did legacy do?" lookups during V1 implementation
>
> The new platform's authoritative API contracts live in `contracts/`.
>
> The original source still lives in `django-auth-core/MOBILE_API_CONTRACT_FULL.md` and may drift; this snapshot is what we cut over **from**.

---

# MOBILE_API_CONTRACT_FULL (Legacy Snapshot)

**Purpose**: Comprehensive, code-verified inventory of the django-auth-core backend API surface. Supersedes `MOBILE_API_CONTRACT.md` (which covered ~40% of endpoints).

**Source of truth**: Derived by reading all 32 `*_urls.py` files in `backend/apps/accounts/` plus their view classes and serializers. Cross-referenced with mobile and web frontend usage analysis.

**Total endpoints documented**: ~180 across all domains.

**Status legend**:
- ✅ used — confirmed mobile consumption
- ❌ mobile-unused — not consumed by mobile (per frontend analysis)
- 🛠 admin-only — staff/superuser endpoints
- ⚠️ unclear — exists in code, mobile usage not confirmed
- 🚫 deprecated — replaced by newer endpoint; do not implement in new platform

**How to read each endpoint**:
- **File**: source url file + view class + line in `views.py` / `*_views.py`
- **Auth**: authentication / permission requirements
- **Mobile usage**: status flag above
- **Request**: key fields with types
- **Response**: status + shape summary
- **Side effects**: DB writes, wallet operations, broadcasts, outbox-equivalent events
- **Notes**: business rules, state machine context, gotchas

---

## Table of Contents

1. Global API Conventions
2. Auth (Register / Login / Refresh / Me)
3. Account & Profile
4. KYC
5. Library / Activity
6. Wallet — MeowPoints
7. Wallet — MeowCredit
8. Wallet — Aggregate Balance
9. Wallet — Prototype (deprecated)
10. Public — Categories
11. Public — Users
12. Public — Creators
13. Public — Store (storefront)
14. Public — Legacy Creators (deprecated)
15. Video — Public Catalog
16. Video — Creator Management
17. Drama — Public/Reader
18. Drama — Creator Management
19. Live — Public/Viewer
20. Live — Creator/Broadcaster
21. Live — Chat (REST)
22. Live — Chat (WebSocket)
23. Live — Payment Methods & Orders
24. Live — Products
25. Gift System
26. Shop — Buyer (catalog)
27. Shop — Cart
28. Shop — Product Orders
29. Shop — QR Resolution
30. Store — Seller Management
31. Store — Seller Applications
32. Store — Seller Orders (creator-facing)
33. Shipping Addresses
34. Membership — Plans / Orders / Me
35. Membership — Manual LBC Verification (6-step)
36. Membership — Billing / Subscription (recurring)
37. Admin — Seller Management
38. Admin — User Management
39. Admin — Video Moderation
40. Admin — Refund Management
41. Admin — Order Settlement
42. Channel (deprecated)
43. State Machine Reference
44. Gaps, Observations, and Inconsistencies
45. Mobile-Unused Endpoint Index (for new-platform exclusion)

---

## 1. Global API Conventions

### Base URL
- All endpoints mounted under `/api/` (from `backend/config/urls.py`).
- WebSocket under `/ws/`.

### Auth header
```
Authorization: Bearer <access_jwt>
```
- JWT issued by SimpleJWT.
- Access token short-lived; refresh available at `/api/auth/refresh/`.

### Content type
- Default: `application/json` for both request and response.
- File uploads: `multipart/form-data` (avatar, video, thumbnail, cover image, KYC documents).

### Pagination
**Inconsistent across endpoints — three patterns observed**:

| Pattern | Used by |
|---|---|
| `?page=N` + `?page_size=N` (default 20, max 100), response `{count, next, previous, results}` | Most list endpoints |
| `?after_id=N` + `?limit=N` (cursor-style) | Live chat messages |
| `?limit=N` + `?offset=N` | Some video comments |
| No pagination | Categories, banners, gifts catalog, packages, shipping addresses, membership plans, shop categories |

**New platform should standardize on cursor pagination.**

### Error response
Existing format (per old MOBILE_API_CONTRACT.md §2):
```json
{
  "detail": "string"
}
```
or
```json
{
  "field_name": ["error message"]
}
```
Some endpoints add `code` field for specific business errors:
```json
{
  "code": "insufficient_balance",
  "detail": "..."
}
```
**Inconsistent.** New platform should standardize on `{"error": {"code", "message", "detail"}}`.

### Time format
ISO 8601 UTC, e.g., `2026-06-04T10:23:45Z`.

### Decimal / Money
**Inconsistent**:
- MeowPoints wallet, MeowCredit wallet: balance as decimal-formatted string `"1234.56"`
- User Balance aggregate: balance as integer
- Product price: decimal `price_amount` + string `price_currency`
- LBC amounts: decimal-formatted string `expected_amount_lbc`

### Currency codes (observed)
| Code | Meaning |
|---|---|
| `MP` | MeowPoints (in UserBalance response) |
| `MC` | MeowCredit (in UserBalance response) |
| `USD` | Product price default |
| `LBC` | LBRY blockchain token |
| `thb_ltt` | Payment method tag for THB-LTT (used in `payment_asset`) |
| `meow_points` | Payment method using points |
| `meow_credit` | Payment method using credits |

### Field naming
- Generally `snake_case`.
- `_url` suffix for absolute URLs.
- `_at` suffix for timestamps.
- Snapshots: `*_snapshot` for historical record fields (e.g., `package_name_snapshot`).
- Aliases for backward compatibility: e.g., `channel_id` aliased to `owner_id`, `subscriber_count` aliased to `follower_count`.

---

## 2. Auth (Register / Login / Refresh / Me)

### POST /api/auth/register
- **File**: `urls.py` → `RegisterAPIView` (`views.py:324`)
- **Auth**: none (AllowAny)
- **Mobile usage**: ✅ used
- **Request**: `email`, `password`, optional `first_name`, `last_name`, `display_name`
- **Response (201)**: `{id, email, display_name, avatar_url, is_creator, is_admin, linked_wallet_id, primary_user_address, wallet_link_status, linked_at}`
- **Side effects**: creates User; **no email verification step**
- **Notes**: `RegisterSerializer` — custom fields

### POST /api/auth/login
- **File**: `urls.py` → `LoginAPIView` (`views.py:329`)
- **Auth**: none (AllowAny)
- **Mobile usage**: ✅ used
- **Request**: `email`, `password`
- **Response (200)**: `{access, refresh, daily_login_reward: {amount, currency, reason, ...}}`
- **Side effects**: **calls `MeowPointService.grant_daily_login_reward(user)` synchronously**; failure in reward grant caught but doesn't fail auth; emits payment event if amount > 0
- **Notes**: Extends `TokenObtainPairView`. **Critical: daily login reward is baked into login response** — mobile relies on this. New platform should preserve this side effect or move it explicit.

### POST /api/auth/refresh
- **File**: `urls.py` → `RefreshAPIView` (`views.py:349`)
- **Auth**: none (AllowAny)
- **Mobile usage**: ✅ used
- **Request**: `refresh` (token)
- **Response (200)**: `{access}`
- **Notes**: standard SimpleJWT refresh. **No rate limit visible.**

### GET /api/auth/me
- **File**: `urls.py` → `MeAPIView` (`views.py:353`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Response (200)**: User object — `{id, email, display_name, first_name, last_name, avatar_url, is_creator, is_admin, linked_wallet_id, primary_user_address, wallet_link_status, linked_at}`
- **Notes**: Used for session validation.

---

## 3. Account & Profile

### GET /api/account/profile/
- **File**: `account_urls.py` → `AccountProfileAPIView` (`views.py:361`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Response (200)**: `{id, email (read-only), display_name, first_name, last_name, avatar, avatar_url, bio, is_creator (read-only), is_seller, is_admin, counts, follower_count, total_gifts, total_likes, total_views, +10 more}`
- **Notes**: Comprehensive profile with content stats and permissions.

### PATCH /api/account/profile/
- **File**: same
- **Auth**: required
- **Mobile usage**: ✅ used
- **Request** (multipart/form-data for avatar): `display_name?`, `first_name?`, `last_name?`, `avatar?` (file), `avatar_clear?` (boolean), `bio?`
- **Response (200)**: same shape as GET
- **Side effects**: updates User; file upload for avatar; `avatar_clear=true` removes avatar

### GET /api/account/preferences/
- **File**: `account_urls.py` → `AccountPreferencesAPIView` (`views.py:381`)
- **Auth**: required
- **Mobile usage**: ⚠️ unclear
- **Response (200)**: `{language: "en-US"|"zh-CN"|"th-TH"|"my-MM", theme: "light"|"dark"|"system", timezone}`

### PATCH /api/account/preferences/
- **File**: same
- **Auth**: required
- **Mobile usage**: ⚠️ unclear
- **Request**: any of the above fields, all optional

### POST /api/account/change-password/
- **File**: `account_urls.py` → `AccountPasswordChangeAPIView` (`views.py:400`)
- **Auth**: required
- **Mobile usage**: ❌ mobile-unused (but web may use)
- **Request**: `current_password`, `new_password`
- **Response (200)**: `{detail: "Password updated successfully."}`
- **Side effects**: validates current password; updates User.password (Django password validators applied)

### GET /api/account/payment-orders/
- **File**: `account_urls.py` → `AccountPaymentOrderListAPIView` (`views.py:412`)
- **Auth**: required
- **Mobile usage**: ❌ mobile-unused
- **Request (query)**: `status?`, `live_stream?`, `product?`, `date_from?`, `date_to?`, `page`, `page_size`
- **Response (200)**: `{count, next, previous, results: [payment orders]}` (paginated 20/page)

### GET /api/account/shipping-addresses/
- **File**: `account_urls.py` → `AccountShippingAddressListCreateAPIView` (`views.py:448`)
- **Auth**: required
- **Mobile usage**: ⚠️ unclear (likely used for shop checkout)
- **Response (200)**: array of `{id, full_name, phone, street_address, city, state, postal_code, country, is_default}` (no pagination)
- **Notes**: **Mounted twice — also at `/api/shipping-addresses/` (see §33).** The latter is what mobile actually uses.

### POST /api/account/shipping-addresses/
- **Same view**; creates address; first address auto-defaults

### GET / PATCH / DELETE /api/account/shipping-addresses/{id}/
- **File**: `AccountShippingAddressDetailAPIView` (`views.py:462`)
- **Notes**: deletion auto-promotes next address to default

### GET /api/account/drama-progress/
- **File**: `account_urls.py` → `AccountDramaProgressListAPIView` (`drama_views.py:407`)
- **Auth**: required
- **Mobile usage**: ⚠️ unclear (Library/Activity History tab likely covers this)
- **Response (200)**: paginated `{series_id, series_title, episode_id, episode_no, progress_seconds, updated_at}`

### GET /api/account/drama-favorites/
- **File**: `AccountDramaFavoritesListAPIView` (`drama_views.py:420`)
- **Auth**: required
- **Mobile usage**: ⚠️ unclear
- **Response (200)**: paginated favorite drama series

---

## 4. KYC

**Flow**: GET `/me/` returns template → POST `/me/` saves data → POST `/documents/` uploads files → POST `/submit/` finalizes.

### GET /api/kyc/me/
- **File**: `kyc_urls.py` → `KycMeAPIView` (`kyc_views.py:17`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Response (200)**: `{status: "not_submitted"|"pending"|"approved"|"rejected", full_name, date_of_birth, nationality, id_type, id_number, id_expiry_date, submitted_at, reviewed_at, reject_reason, documents: {id_front?: {...}, selfie?: {...}}}`
- **Notes**: Returns not-submitted template if no profile exists.

### POST /api/kyc/me/ (also PATCH)
- **Same view**
- **Request**: `full_name`, `date_of_birth` (YYYY-MM-DD), `nationality` (2-char code), `id_type`, `id_number`, `id_expiry_date`
- **Side effects**: creates/updates KycProfile; transitions status → `pending`; sets `submitted_at`

### POST /api/kyc/documents/
- **File**: `KycDocumentUploadAPIView` (`kyc_views.py:58`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Request** (multipart): `document_type: "id_front"|"selfie"`, `image` (file)
- **Response (200)**: `{document_type, image_url, uploaded_at}`
- **Side effects**: creates/updates KycDocument; if profile was approved, **resets to pending**
- **Notes**: replaces previous document of same type

### POST /api/kyc/submit/
- **File**: `KycSubmitAPIView` (`kyc_views.py:70`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Response (200)**: KycProfile object
- **Response (400)**: validation error if required fields missing OR required documents not uploaded
- **Side effects**: status → `pending`, `submitted_at` = now, clears `reviewed_at` and `reject_reason`

---

## 5. Library / Activity

Mobile labels this UI as **"Activity"**, not "Library". Five tabs, all confirmed mobile-used.

### GET /api/account/library/history/
- **File**: `library_urls.py` → `AccountLibraryHistoryAPIView` (`library_views.py:82`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Response (200)**: paginated unified history items:
  - `type: "drama"|"video"`, `id`, `title`, `cover_url|thumbnail_url`, `progress_seconds` (0 for video), `duration_seconds` (0 for video), `updated_at`, `series_id?`, `episode_id?`, `episode_no?` (drama only)
- **Notes**: Combines drama watch progress + video views; sorted by last update.

### GET /api/account/library/liked/
- **File**: `AccountLibraryLikedAPIView` (`library_views.py:126`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Response (200)**: paginated liked videos: `{type: "video", id, title, thumbnail_url, liked_at}`

### GET /api/account/library/purchased/
- **File**: `AccountLibraryPurchasedAPIView` (`library_views.py:148`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Response (200)**: paginated mixed:
  - Drama unlocks: `{type: "drama_episode", series_id, payment_method, ...}`
  - Payment orders: `{type: "order", amount, currency, status}`
  - Memberships: `{type: "membership", starts_at, ends_at, status}`
- **Notes**: combines three purchase sources sorted by purchase date desc.

### GET /api/account/library/gifts/sent/
- **File**: `AccountLibraryGiftsSentAPIView` (`library_views.py:201`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Response (200)**: paginated `{id, direction: "sent", gift_name, amount, points_amount, credits_amount, receiver: {id, name}, content: {type: "video"|"drama"|"live_stream", id, title}, created_at}`

### GET /api/account/library/gifts/received/
- **File**: `AccountLibraryGiftsReceivedAPIView` (`library_views.py:225`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Response (200)**: same structure with `sender` instead of `receiver`

---

## 6. Wallet — MeowPoints

### GET /api/meow-points/wallet/
- **File**: `meow_points_urls.py` → `MeowPointWalletAPIView` (`meow_points_views.py:25`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Response (200)**: `{balance: "1234.56", balance_display, total_earned, total_spent, total_purchased, total_bonus, created_at, updated_at}` (all amounts as decimal strings)
- **Side effects**: **auto-creates wallet on first access** (with warning log)

### GET /api/meow-points/packages/
- **File**: `MeowPointPackageListAPIView` (`meow_points_views.py:36`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Response (200)**: array of active packages: `{code, name, points_amount, bonus_points, total_points, price_amount, price_currency, status: "active", sort_order, description}`

### GET /api/meow-points/ledger/
- **File**: `MeowPointLedgerListAPIView` (`meow_points_views.py:44`)
- **Auth**: required
- **Mobile usage**: ⚠️ unclear
- **Request (query)**: `page`, `page_size` (default 20, max 100)
- **Response (200)**: paginated ledger entries:
  - `{id, entry_type, amount, balance_before, balance_after, target_type: "live_gift"|"content_unlock"|..., target_id, payment_order_id?, note, created_at}`

### GET /api/meow-points/orders/
- **File**: `MeowPointOrderListCreateAPIView` (`meow_points_views.py:53`)
- **Auth**: required
- **Mobile usage**: ⚠️ unclear
- **Response (200)**: paginated MeowPointPurchase objects
- **Side effects**: **auto-credits paid purchases** via `credit_paid_purchase()` even on GET

### POST /api/meow-points/orders/
- **Same view**
- **Request**: `package_code`
- **Response (201)**: full purchase object incl. `order_no`, `package_*_snapshot`, `payment_order_status`, `txid`, `paid_at`, `credited_at`

### GET /api/meow-points/orders/{order_no}/
- **File**: `MeowPointOrderDetailAPIView` (`meow_points_views.py:75`)
- **Side effects**: auto-credits if payment detected

### POST /api/meow-points/orders/{order_no}/tx-hint/
- **File**: `MeowPointOrderTxHintAPIView` (`meow_points_views.py:90`)
- **Auth**: required
- **Mobile usage**: ❌ mobile-unused (internal tx submission)
- **Request**: `txid`
- **Response (200)**: `{order_no, txid_hint, status, detail}`

### POST /api/meow-points/daily-login-reward/
- **File**: `DailyLoginRewardClaimAPIView` (`meow_points_views.py:124`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Request**: none
- **Response (200)**: `{awarded: boolean, amount?: integer, message: string}`
- **Side effects**: creates MeowPointLedger entry; updates wallet balance; **service enforces once-per-day limit**
- **Notes**: Also implicitly granted in `/api/auth/login/` response; this endpoint is explicit claim path (used by MeowPointsPage "Claim" button).

---

## 7. Wallet — MeowCredit

### GET /api/meow-credit/wallet/
- **File**: `meow_credit_urls.py` → `MeowCreditWalletAPIView` (`meow_credit_views.py:30`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Response (200)**: `{balance: "1234.56", balance_display, total_recharged, total_spent, total_redeemed, total_adjusted, created_at, updated_at}` (decimal strings)
- **Side effects**: auto-creates wallet on first access

### GET /api/meow-credit/packages/
- **File**: `MeowCreditPackageListAPIView` (`meow_credit_views.py:41`)
- **Auth**: required
- **Mobile usage**: ✅ used (presumed)
- **Response (200)**: active credit packages

### GET /api/meow-credit/ledger/
- **File**: `MeowCreditLedgerListAPIView` (`meow_credit_views.py:49`)
- **Auth**: required
- **Mobile usage**: ⚠️ unclear
- **Response (200)**: paginated `{id, entry_type, status: "pending"|"completed", amount, balance_before, balance_after, target_type, target_id, payment_order_id?, note, created_at}`

### GET /api/meow-credit/recharge-info/
- **File**: `MeowCreditRechargeInfoAPIView` (`meow_credit_views.py:58`)
- **Auth**: required
- **Mobile usage**: ✅ used (needed before creating recharge)
- **Request (query)**: `package_code`
- **Response (200)**: `{package_code, package_name, credit_amount, bonus_credit, total_credit, price_amount, price_currency, display_currency, expected_amount, pay_to_address, required_confirmations, notice}`
- **Response (503)**: if `LBRY_PLATFORM_RECEIVE_ADDRESS` not configured

### POST /api/meow-credit/recharges/
- **File**: `MeowCreditRechargeListCreateAPIView` (`meow_credit_views.py:136`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Request**: `package_code`
- **Response (201)**: MeowCreditRecharge with `order_no`, `expected_amount`, `pay_to_address`, `expires_at`, `status`, `payment_order_status`, `txid`, `paid_at`, `credited_at`
- **Side effects**: creates Recharge + PaymentOrder

### GET /api/meow-credit/recharges/
- **Same view**
- **Side effects**: auto-credits paid recharges

### POST /api/meow-credit/recharges/submit-txid/
- **File**: `MeowCreditRechargeSubmitTxidAPIView` (`meow_credit_views.py:96`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Request**: `package_code`, `txid`
- **Response**: 201 (new) or 200 (existing) — recharge object + `verification` object
- **Side effects**: creates/updates Recharge; attempts immediate verification

### GET /api/meow-credit/recharges/{order_no}/
- **File**: `MeowCreditRechargeDetailAPIView` (`meow_credit_views.py:157`)

### POST /api/meow-credit/recharges/{order_no}/tx-hint/
- **File**: `MeowCreditRechargeTxHintAPIView` (`meow_credit_views.py:172`)
- **Mobile usage**: ⚠️ unclear

### POST /api/meow-credit/recharges/{order_no}/verify-now/
- **File**: `MeowCreditRechargeVerifyNowAPIView` (`meow_credit_views.py:204`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Request (optional)**: `txid` — if provided, updates hint and re-verifies
- **Response (200)**: `{recharge, verification, detail}`
- **Response (502)**: blockchain lookup failure
- **Side effects**: attempts chain verification; may credit wallet

### POST /api/meow-credit/redeems/
- **File**: `MeowCreditRedeemListCreateAPIView` (`meow_credit_views.py:237`)
- **Auth**: required
- **Mobile usage**: ❌ mobile-unused (admin workflow)
- **Request**: `amount` (min 1), `redeem_method`, `account_snapshot?` (object)
- **Response (201)**: `{redeem_no, amount, status: "pending", redeem_method, account_snapshot, reviewed_at, reject_reason, created_at, updated_at}`

### GET /api/meow-credit/redeems/
- **Same view**; paginated list

---

## 8. Wallet — Aggregate Balance

### GET /api/user-balance/balance/
- **File**: `user_balance_urls.py` → `UserBalanceAPIView` (`gift_views.py:216`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Response (200)**:
```json
{
  "meow_points": {"balance": 1234, "currency": "MP"},
  "meow_credit": {"balance": 56, "currency": "MC"},
  "coins": 1234,
  "currency": "MP"
}
```
- **Side effects**: auto-creates wallets if missing
- **Notes**: **`coins` and top-level `currency` are legacy aliases for meow_points**; new platform should drop these. Balance returned as **integer** (inconsistent with wallet-detail endpoints which return string).

---

## 9. Wallet — Prototype (deprecated)

🚫 **Mark as deprecated. New platform should NOT implement.**

### POST /api/wallet-prototype/pay-order/
- **File**: `wallet_prototype_urls.py` → `WalletPrototypePayOrderAPIView` (`views.py:3657`)
- **Auth**: required
- **Mobile usage**: ❌ mobile-unused
- **Request**: `order_no`, `wallet_id?` (defaults to user's linked_wallet_id), `password`
- **Response (200)**: `{ok, txid?, verification?}`
- **Notes**: prototype; requires linked wallet + password to sign blockchain transaction. Replaced by `verify-now` endpoints.

### POST /api/wallet-prototype/pay-product-order/
- Same shape, specifically for ProductOrder

---

## 10. Public — Categories

### GET /api/public/categories/
- **File**: `public_category_urls.py` → `PublicCategoryListAPIView` (`views.py:3892`)
- **Auth**: none
- **Mobile usage**: ✅ used (shop filtering)
- **Response (200)**: array of `{id, name, slug, description?, cover_image_url?, sort_order}` (no pagination)
- **Notes**: Excludes legacy slug aliases; ordered by sort_order then name.

---

## 11. Public — Users

### GET /api/public/users/{user_id}/
- **File**: `public_user_urls.py` → `PublicUserDetailAPIView` (`views.py:2945`)
- **Auth**: none
- **Mobile usage**: ✅ used
- **Response (200)**: `{id, email (masked for non-auth), display_name, avatar_url, follower_count, is_creator, bio, created_at, +10 more}`

### POST /api/public/users/{user_id}/follow/
- **File**: `PublicUserFollowAPIView` (`views.py:2954`)
- **Auth**: required
- **Mobile usage**: ✅ used (**new canonical follow path**)
- **Response (200)**: `{user_id, is_following: true, viewer_is_following: true, follower_count, subscriber_count (alias), is_subscribed (alias), viewer_is_subscribed (alias)}`
- **Side effects**: creates ChannelSubscription; updates follower count
- **Notes**: rejects self-follow

### DELETE /api/public/users/{user_id}/follow/
- **Same view**; unfollow

### GET /api/public/users/{user_id}/followers/
- **File**: `PublicUserFollowersListAPIView` (`views.py:3009`)
- **Auth**: none
- **Mobile usage**: ⚠️ unclear
- **Request (query)**: `?page=N`
- **Response (200)**: paginated `{count, results: [{id, display_name, avatar_url, follower_count, viewer_is_following}]}`

### GET /api/public/users/{user_id}/following/
- **File**: `PublicUserFollowingListAPIView` (`views.py:3018`)
- Similar shape

---

## 12. Public — Creators

### GET /api/public/creators/{creator_id}/
- **File**: `public_creator_urls.py` → `PublicCreatorDetailAPIView` (`views.py:2936`)
- **Auth**: none
- **Mobile usage**: ✅ used
- **Response (200)**: `{id, display_name, avatar_url, bio, follower_count, is_creator: true, +15 more incl. membership plan summary + store details}`
- **Notes**: filters to `is_creator=True` users.

### GET /api/public/creators/{creator_id}/videos/
- **File**: `PublicCreatorVideoListAPIView` (`views.py:3027`)
- **Auth**: none
- **Mobile usage**: ✅ used
- **Response (200)**: paginated (20/page) `{count, results: [{id, title, thumbnail_url, created_at, view_count, like_count}]}`

### GET /api/public/creators/{creator_id}/dramas/
- **File**: `PublicCreatorDramaListAPIView` (`views.py:3067`)
- **Mobile usage**: ⚠️ unclear (mobile CreatorProfile drama tab uses similar)
- **Response (200)**: paginated drama objects

### GET /api/public/creators/{creator_id}/lives/
- **File**: `PublicCreatorLiveListAPIView` (`views.py:3086`)
- **Mobile usage**: ⚠️ unclear (CreatorProfile live tab — but live tile tap is a placeholder snackbar)

---

## 13. Public — Store (storefront)

### GET /api/stores/{store_slug}/
- **File**: `public_store_urls.py` → `PublicSellerStoreDetailAPIView` (`views.py:1265`)
- **Auth**: none (owner sees inactive)
- **Mobile usage**: ✅ used
- **Response (200)**: `{id, slug, name, description, owner: {id, display_name, avatar_url}, is_active, created_at, updated_at}`

### GET /api/stores/{store_slug}/products/
- **File**: `PublicSellerStoreProductListAPIView` (`views.py:1278`)
- **Auth**: none (owner sees drafts)
- **Mobile usage**: ✅ used
- **Response (200)**: array (no pagination) of products: `{id, title, description, price_amount, price_currency, meow_points_price?, meow_credit_price?, cover_image, stock_quantity, status}`

---

## 14. Public — Legacy Creators (deprecated)

🚫 **Mobile has migrated to `/api/public/users/{id}/follow/`. Do NOT implement in new platform.**

### POST /api/creators/{creator_id}/follow/
- **File**: `creators_urls.py` → `CreatorFollowAPIView` (`views.py:2889`)
- **Auth**: required
- **Mobile usage**: ❌ mobile-unused
- **Notes**: **web frontend still uses this path** — cutover requires web to migrate first.

### DELETE /api/creators/{creator_id}/follow/
- Same view; unfollow

---

## 15. Video — Public Catalog

### GET /api/public/videos/
- **File**: `public_video_urls.py` → `PublicVideoListAPIView` (`views.py:2508`)
- **Auth**: none
- **Mobile usage**: ✅ used
- **Request (query)**: `category` (slug), `access_type`, `search`, `ordering`
- **Response (200)**: paginated `VideoSerializer`: `{id, title, description, owner_id, owner_name, owner_avatar_url, owner_is_creator, like_count, comment_count, view_count, category, visibility, file_url, thumbnail_url, is_liked, can_watch, +15 more}`
- **Notes**: filters `visibility=PUBLIC`, `status=ACTIVE`; **masks locked file fields**.

### GET /api/public/videos/{id}/
- **File**: `PublicVideoDetailAPIView` (`views.py:2545`)
- **Auth**: none
- **Mobile usage**: ✅ used
- **Notes**: does **not** increment view_count (see `/view/`).

### GET /api/public/videos/{id}/interaction-summary/
- **File**: `PublicVideoInteractionSummaryAPIView` (`views.py:2672`)
- **Auth**: optional
- **Mobile usage**: ✅ used (heavy — refreshes counts)
- **Response (200)**: `{video_id, like_count, comment_count, view_count, share_count, gift_count, gift_points_total, gift_amount_total, is_liked, viewer_is_following, follower_count, channel_id (alias for owner_id), subscriber_count (alias)}`

### GET /api/public/videos/{id}/related/
- **File**: `PublicRelatedVideoListAPIView` (`views.py:2564`)
- **Mobile usage**: ⚠️ unclear

### GET /api/public/videos/{id}/recommendations/
- **File**: `VideoRecommendationsAPIView` (`views.py:2601`)
- **Mobile usage**: ⚠️ unclear

### POST /api/public/videos/{id}/like/
- **File**: `VideoLikeAPIView` (`views.py:2826`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Response (200)**: VideoInteractionSummary (updated)
- **Side effects**: creates VideoLike; increments `video.like_count` via `F()` update
- **Notes**: idempotent

### DELETE /api/public/videos/{id}/like/
- **Same view (line 2839)**; unlike

### GET /api/public/videos/{id}/comments/
- **File**: `PublicVideoCommentListAPIView` (`views.py:3810`)
- **Auth**: none
- **Mobile usage**: ✅ used
- **Request (query)**: `limit`, `offset`
- **Response (200)**: paginated comments (supports threading via `parent_id`)

### POST /api/public/videos/{id}/comments/
- **File**: `VideoCommentCreateAPIView` (`views.py:3834`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Request**: `content`, `parent_id?`
- **Response (201)**: comment
- **Side effects**: creates VideoComment; increments `video.comment_count`; if reply, increments `parent.reply_count`

### POST /api/public/videos/{id}/share/
- **File**: `PublicVideoShareTrackAPIView` (`views.py:2690`)
- **Auth**: optional
- **Mobile usage**: ✅ used
- **Request**: `channel?` (max 64 chars)
- **Response (200)**: `{video_id, share_count, channel}`
- **Side effects**: creates VideoShare record; logs IP, user_agent

### POST /api/public/videos/{id}/view/
- **File**: `PublicVideoViewTrackAPIView` (`views.py:3868`)
- **Auth**: optional
- **Mobile usage**: ✅ used
- **Response (200)**: `{video_id, view_count}`
- **Side effects**: creates VideoView; rate-limited (dedupe per user/IP per minute)

### POST /api/public/videos/{id}/gifts/send/
- **File**: `PublicVideoGiftSendAPIView` (`views.py:2722`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Request**: `amount` ∈ {1, 10, 30, 100, 200, 500}, `payment_method: "meow_points"|"meow_credit"` (default `"meow_points"`)
- **Response (201)**: ContentGiftSendResponse (see §25)
- **Side effects**: `GiftService.send_content_gift()`; debits sender wallet; credits receiver wallet; creates `GiftTransaction(target_type=TARGET_VIDEO)`
- **Errors**: 400 `code=insufficient_balance`

---

## 16. Video — Creator Management

### GET /api/videos/
- **File**: `video_urls.py` → `VideoListCreateAPIView` (`views.py:2431`)
- **Auth**: required
- **Mobile usage**: ⚠️ unclear (creator's own videos)
- **Request (query)**: `category`, `access_type`, `search`, `ordering`; pagination

### POST /api/videos/
- **Same view**
- **Auth**: required
- **Mobile usage**: ⚠️ unclear
- **Request** (multipart): `title`, `description`, `file`, `thumbnail`, `category`, `access_type`, `visibility`, `preview_seconds`
- **Side effects**: creates Video; auto-generates thumbnail; **transcodes async (placeholder — no real transcoder)**

### GET / PATCH / DELETE /api/videos/{id}/
- **File**: `VideoDetailAPIView` (`views.py:2471`)
- **Notes**: PATCH uses `VideoMetadataSerializer` (subset of fields)

### POST /api/videos/{id}/regenerate-thumbnail/
- **File**: `VideoRegenerateThumbnailAPIView` (`views.py:2485`)
- **Request**: `time_offset?` (float, default 1.0)
- **Response (200)**: `{thumbnail_url, updated_at}`
- **Side effects**: extracts frame at time_offset; regenerates thumbnail

### GET / POST /api/creators/videos/
- **File**: `creator_video_urls.py` → `CreatorVideoListCreateAPIView` (`views.py:2467`)
- **Auth**: required + `IsCreator`
- **Mobile usage**: ⚠️ unclear
- **Notes**: extends `VideoListCreateAPIView` with creator permission.

---

## 17. Drama — Public/Reader

### GET /api/dramas/
- **File**: `drama_urls.py` → `DramaSeriesListAPIView` (`drama_views.py:63`)
- **Auth**: optional
- **Mobile usage**: ✅ used
- **Request (query)**: `category` (integer ID or slug); pagination
- **Response (200)**: paginated `DramaSeriesSerializer`: `{id, title, description, cover_url, tags, total_episodes, free_episode_count, locked_episode_count, view_count, favorite_count, comment_count, share_count, gift_count, gift_amount_total, is_favorited, continue_episode_no, continue_progress_seconds, owner_id, owner_name, owner_avatar_url, owner_is_creator, channel_id (alias), channel_name (alias), viewer_is_following, is_following_owner, follower_count, subscriber_count (alias), +5 more}`
- **Notes**: filters `is_active=True`, `status=PUBLISHED`. If authenticated, includes favorite/progress context.

### GET /api/dramas/{id}/
- **File**: `DramaSeriesDetailAPIView` (`drama_views.py:107`)
- **Auth**: optional
- **Mobile usage**: ✅ used

### GET /api/dramas/{id}/episodes/
- **File**: `DramaEpisodeListAPIView` (`drama_views.py:137`)
- **Auth**: optional
- **Mobile usage**: ✅ used
- **Response (200)**: `{series_id, episodes: [DramaEpisodeSerializer]}`
- **Notes**: each episode includes `is_locked`, `is_unlocked`, `can_watch`, `points_price`, `credits_price`

### GET /api/dramas/{id}/episodes/{episode_no}/
- **File**: `DramaEpisodeDetailAPIView` (`drama_views.py:168`)
- **Auth**: optional
- **Mobile usage**: ✅ used
- **Response (200)**: single episode incl. `playback_url`, `hls_url` (only if unlocked), `video_url`, `previous_episode_no`, `next_episode_no`

### POST /api/dramas/episodes/{episode_id}/unlock/
- **File**: `DramaEpisodeUnlockAPIView` (`drama_views.py:433`)
- **Auth**: required
- **Mobile usage**: ✅ used (critical monetization)
- **Request**: `payment_method: "meow_points"|"meow_credit"` (default `"meow_points"`)
- **Response (200)**: `DramaUnlockResponseSerializer`: `{episode_id, series_id, is_unlocked, payment_method, points_charged, credits_charged, code?: "already_unlocked"}`
- **Side effects**: `DramaAccessService.unlock_with_meow_points()` or `unlock_with_meow_credit()`; creates DramaUnlock; debits wallet
- **Notes**: **4 unlock methods exist**: free, meow_points, meow_credit, **membership** (handled elsewhere — see Drama unlock methods in §43).
- **Errors**: 400 `code=insufficient_balance`

### GET /api/dramas/{id}/progress/
- **File**: `DramaProgressUpsertAPIView` (`drama_views.py:202`, GET behavior implicit)
- **Auth**: required
- **Mobile usage**: ✅ used

### POST /api/dramas/{id}/progress/
- **Same view**
- **Request**: `episode_id`, `progress_seconds` (>= 0), `completed` (boolean)
- **Response (200)**: `DramaWatchProgressSerializer`: `{series_id, episode_id, episode_no, progress_seconds, completed, updated_at}`
- **Side effects**: upsert DramaWatchProgress

### POST /api/dramas/episodes/{episode_id}/progress/
- **File**: `DramaEpisodeProgressUpsertAPIView` (`drama_views.py:566`)
- **Notes**: same shape as series-scope progress; presumably finer grain

### POST / DELETE /api/dramas/{id}/favorite/
- **File**: `DramaFavoriteAPIView` (`drama_views.py:230`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Response (200)**: `{series_id, is_favorited, favorite_count}`
- **Side effects**: creates/deletes DramaFavorite; atomic count update

### POST /api/dramas/{id}/gifts/send/
- **File**: `DramaGiftSendAPIView` (`drama_views.py:343`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Request**: `amount` ∈ {1, 10, 30, 100, 200, 500}, `payment_method`
- **Response (201)**: DramaGiftSendResponse (similar to video gift)
- **Side effects**: `GiftService.send_drama_gift()`; creates `GiftTransaction(target_type=TARGET_DRAMA_SERIES)`; **no WebSocket broadcast**

### GET / POST /api/dramas/{id}/comments/
- **File**: `DramaCommentListCreateAPIView` (`drama_views.py:264`)
- **Mobile usage**: ✅ used
- **Request**: `content`, `parent_id?`
- **Side effects**: creates DramaComment; increments series.comment_count

### POST /api/dramas/{id}/share/
- **File**: `DramaShareAPIView` (`drama_views.py:310`)
- **Auth**: optional
- **Mobile usage**: ✅ used
- **Request**: `channel?` (max 64)

### GET /api/dramas/{id}/interaction-summary/
- **File**: `DramaInteractionSummaryAPIView` (`drama_views.py:332`)
- **Mobile usage**: ✅ used
- **Response (200)**: mirrors video interaction-summary

### POST /api/dramas/{id}/view/
- **File**: `DramaSeriesViewTrackAPIView` (`drama_views.py:585`)
- **Mobile usage**: ✅ used
- **Side effects**: creates DramaView; same dedup as video views

---

## 18. Drama — Creator Management

❌ All mobile-unused. Creator drama management is desktop/admin only.

### GET / POST /api/creators/dramas/
- **File**: `creator_drama_urls.py` → `CreatorDramaSeriesListCreateAPIView` (`drama_views.py:494`)
- **Auth**: `IsCreatorOrAdmin`

### GET / PATCH / DELETE /api/creators/dramas/{id}/
- **File**: `CreatorDramaSeriesDetailAPIView` (`drama_views.py:509`)
- **Notes**: DELETE is soft (`is_active=False`)

### GET / POST /api/creators/dramas/{id}/episodes/
- **File**: `CreatorDramaEpisodeListCreateAPIView` (`drama_views.py:524`)
- **Request** (multipart): `title`, `file`, `episode_no`, `is_free`, `meow_points_price`, `meow_credit_price`

### GET / PATCH / DELETE /api/creators/dramas/{id}/episodes/{episode_id}/
- **File**: `CreatorDramaEpisodeDetailAPIView` (`drama_views.py:546`)
- **DELETE**: soft; recounts total_episodes

---

## 19. Live — Public/Viewer

### GET /api/live/
- **File**: `live_urls.py` → `LiveStreamListAPIView` (`views.py:1863`)
- **Auth**: optional
- **Mobile usage**: ✅ used
- **Response (200)**: list of `LiveStreamSerializer` (**no pagination, `pagination_class=None`**)

### GET /api/live/{id}/
- **File**: `LiveStreamDetailAPIView` (`views.py:2072`)
- **Auth**: optional
- **Mobile usage**: ✅ used
- **Notes**: if owner, response includes `stream_key` + `publish_config` (WebRTC/RTMP config from Ant Media)

### GET /api/live/{id}/status/
- **File**: `LiveStreamStatusDetailAPIView` (`views.py:2100`)
- **Auth**: optional
- **Mobile usage**: ✅ used (polls for live status)
- **Response (200)**: `{id, status, effective_status, can_start, can_end, viewer_count, publish: {connected, status}, play: {connected, status}, message}`
- **Notes**: `effective_status` is normalized from Ant Media (may differ from `status`).

### GET /api/live/{id}/watch-config/
- **File**: `LiveStreamWatchConfigAPIView` (`views.py:2155`)
- **Auth**: optional
- **Mobile usage**: ✅ used (**critical for playback**)
- **Response (200)**:
```json
{
  "live_id": 123,
  "status": "idle|ready|live|ended|failed",
  "effective_status": "idle|publishing|live|ended|failed",
  "viewer_count": 45,
  "playback": {
    "mode": "webrtc|hls",
    "stream_id": "abc123xyz",
    "websocket_url": "wss://ant-media-server/websocket",
    "hls_url": "https://cdn/stream.m3u8",
    "connected": true
  },
  "fallback": {"mode": "hls", "hls_url": "https://cdn/stream.m3u8"},
  "thumbnail_url": "https://...",
  "preview_image_url": "https://...",
  "snapshot_url": "https://..."
}
```
- **Side effects**: increments `viewer_count` once per unique user/IP per 60s (cache-based dedup); calls `AntMediaLiveAdapter`

### GET /api/live/quick-start/
- **File**: `LiveStreamQuickStartAPIView` (`views.py:1930`)
- **Auth**: required + `IsCreator`
- **Mobile usage**: ⚠️ unclear (creator endpoint, but mobile may call from creator flow)
- **Request**: `category?`, `visibility: "public"|"private"`, `fresh?` (boolean)
- **Side effects**: reuses/creates LiveStream; `fresh=true` stops zombie streams via Ant Media

### POST /api/live/{id}/prepare/
- **File**: `LiveStreamPrepareAPIView` (`views.py:2341`)
- **Auth**: `IsCreator`
- **Mobile usage**: ⚠️ unclear (creator)

### POST /api/live/{id}/start/
- **File**: `LiveStreamStatusAPIView(new_status='live')` (`views.py:2240`)
- **Auth**: `IsCreator`
- **Mobile usage**: ⚠️ unclear
- **Request**: `publish_session_id?`; query `skip_ant_media`
- **Response (200)**: `{ok: true, status: "live", already_started: boolean, live: LiveStreamSerializer}`
- **Notes**: state machine — only IDLE/READY → LIVE allowed; `skip_ant_media=true` only if `DEBUG=true` or `ALLOW_LIVE_START_BYPASS=true`

### POST /api/live/{id}/end/
- **Same view, `new_status='ended'`**
- **Auth**: `IsCreator`
- **Response (200)**: `{ok: true, status: "ended", live: LiveStreamSerializer}`
- **Notes**: terminal state — cannot restart

### GET /api/live/{id}/products/
- **File**: `LiveStreamProductPublicListAPIView`
- **Mobile usage**: ✅ used
- **Notes**: products linked to this live for promotion

### GET /api/live/{id}/gifts/
- **File**: `LiveGiftListAPIView` (`gift_views.py:35`)
- **Auth**: optional
- **Mobile usage**: ✅ used
- **Response (200)**: list of `GiftSerializer`: `{id, code, name, emoji, coin_cost, points_price, icon_url, animation_url, is_active, sort_order}`
- **Notes**: static gift catalog; live_id only validates stream exists

### POST /api/live/{id}/gifts/send/
- **File**: `LiveGiftSendAPIView` (`gift_views.py:74`)
- **Auth**: required
- **Mobile usage**: ✅ used (**critical for live monetization**)
- **Request — two modes**:
  - **Amount mode (preferred)**: `amount` ∈ {1,10,30,100,200,500}, `payment_method: "meow_points"|"meow_credit"`
  - **Fixed gift mode (legacy)**: `gift_id` OR `gift_code`, `quantity`
- **Response**: 201 (new) or 200 (duplicate within 2-sec window)
  - Amount mode: `{ok: true, event: {id, type, message, payload}, transaction: GiftTransactionSerializer, sender_balance, receiver_balance}`
  - Fixed mode: `GiftTransactionSerializer`
- **Side effects**:
  - Debits sender wallet
  - Credits receiver wallet
  - Creates `GiftTransaction(target_type=TARGET_LIVE_STREAM)`
  - Creates `LiveChatMessage(type=TYPE_GIFT)` with payload
  - **Broadcasts** via WebSocket to `live_chat_{stream_id}` group
- **Notes**: fixed mode uses 2-second dedup window. **This is the only gift endpoint that triggers WebSocket broadcast** (video/drama gifts are silent).

---

## 20. Live — Creator/Broadcaster

❌ Mobile-unused unless noted. These are creator dashboard/management endpoints.

### GET /api/creators/live/
- **File**: `creator_live_urls.py` → `CreatorLiveStreamListAPIView` (`views.py:1884`)
- **Auth**: required + `IsCreator`
- **Response (200)**: paginated creator's own streams (max page_size 100)

### GET /api/live/health/
- **File**: `LiveStreamHealthAPIView` (`views.py:1908`)
- **Auth**: `IsCreatorOrStaff`
- **Mobile usage**: ❌ mobile-unused
- **Response (200)**: `{ant_media_base_url_configured, ant_media_app_name, websocket_url_configured, rest_app_name, udp_ports_note, ok}`

### POST /api/live/create/
- **File**: `LiveStreamCreateAPIView` (`views.py:1898`)
- **Auth**: required + `IsCreator`
- **Request**: `title`, `description`, `category?`, `visibility: "public"|"private"`, `thumbnail?` (file)
- **Side effects**: creates LiveStream; generates stream_key; STATUS_IDLE

### PATCH /api/live/{id}/update/
- **File**: `LiveStreamUpdateAPIView` (`views.py:2230`)
- **Auth**: owner only
- **Request**: partial metadata

---

## 21. Live — Chat (REST)

### GET /api/live/{id}/chat/messages/
- **File**: `live_urls.py` → `LiveChatMessageListCreateAPIView` (`views.py:1543`)
- **Auth**: optional (visibility-restricted)
- **Mobile usage**: ✅ used (chat polling fallback for WebSocket)
- **Request (query)**: `after_id?` (cursor), `limit?` (1-100, default 50)
- **Response (200)**: `{results: [LiveChatMessageSerializer], next_after_id}` (cursor-based)
- **Notes**: filters `is_deleted=False`

### POST /api/live/{id}/chat/messages/
- **Same view** (`views.py:1592`)
- **Auth**: required
- **Mobile usage**: ✅ used (REST fallback for sending)
- **Request**: `content`, `product_id?` (for product mentions)
- **Response (201)**: `LiveChatMessageSerializer`
- **Side effects**: creates LiveChatMessage; broadcasts to `live_chat_{stream_id}` WebSocket group; validates stream is LIVE

### PUT /api/live/{id}/chat/messages/{message_id}/pin/
- **File**: `LiveChatMessageModerationAPIView`
- **Auth**: required (broadcaster only)
- **Mobile usage**: ❌ mobile-unused

### DELETE /api/live/{id}/chat/messages/{message_id}/
- **Same view**
- **Auth**: broadcaster or author
- **Side effects**: soft-delete (`is_deleted=True`); broadcasts deletion event

---

## 22. Live — Chat (WebSocket)

### Connection
- **URL**: `ws://backend/ws/live/<live_id>/chat/`
- **File**: `ws_urls.py` → `LiveChatConsumer` (`consumers.py:8`)
- **Auth**: required (checked in `connect()`)
- **Mobile usage**: ✅ used (primary real-time path)
- **Validation**: extracts `live_id` from URL; checks user authenticated; checks stream exists; checks visibility (public OR user is owner)
- **Close codes**: 4401 (auth fail), 4403 (permission denied)

### Inbound message: `post_message`
```json
{
  "action": "post_message",
  "data": {
    "content": "<string, required>",
    "product_id": "<integer, optional, for product mention>"
  }
}
```
- **Handler**: `receive_json()` (`consumers.py:26`)
- **Validation**: `LiveChatMessageCreateSerializer`
- **Side effects**: creates LiveChatMessage; broadcasts to group
- **Error response**: `{type: "error", detail|errors: {...}}`

### Outbound: `message_created`
```json
{
  "type": "message_created",
  "message": {
    "id": "<integer>",
    "live_id": "<integer>",
    "type": "text|product|gift",
    "message_type": "MESSAGE|PRODUCT|GIFT",
    "content": "<string>",
    "payload": {},
    "created_at": "<ISO8601>",
    "is_pinned": "<boolean>",
    "user": {"id": "<int>", "name": "<display_name>", "avatar_url": "<string>"},
    "product": "<object or null>"
  }
}
```
- **Source**: `chat_message()` handler responds to server-side `group_send(type='chat.message', event='message_created', message=...)`
- **Broadcast group**: `live_chat_{live_id}`
- **Triggers**:
  - REST `POST /chat/messages/`
  - Live gift send (creates Live ChatMessage with type=GIFT)
  - Pin/delete moderation events
- **Payload variants**: GIFT carries `{sender_id, sender_name, amount, payment_method}`; PRODUCT carries product details

### Outbound: `error`
```json
{"type": "error", "detail": "Unsupported action."}
```

### Disconnect
- Removes client from group via `group_discard()`
- **Note**: no viewer count decrement; viewer count is handled by HTTP `/watch-config/` dedup.

---

## 23. Live — Payment Methods & Orders

❌ All mostly mobile-unused; creator setup + payment-side scaffolding.

### GET /api/live/{id}/payment-methods/
- **File**: `LivePaymentMethodPublicListAPIView` (`views.py:1739`)
- **Mobile usage**: ⚠️ unclear

### GET / POST /api/live/{id}/payment-methods/manage/
- **File**: `LivePaymentMethodManageListCreateAPIView` (`views.py:1686`)
- **Auth**: owner only

### GET / PATCH / DELETE /api/live/{id}/payment-methods/manage/{pm_id}/
- **File**: `LivePaymentMethodManageDetailAPIView`

### POST /api/live/{id}/payments/orders/
- **File**: `LivePaymentOrderCreateAPIView` (`views.py:1766`)
- **Auth**: required
- **Mobile usage**: ⚠️ unclear
- **Request**: `payment_method_id`, `amount`
- **Response (201)**: `{order_id, status, payment_url?}`

### GET /api/live/{id}/payments/orders/{order_id}/
- **File**: `LivePaymentOrderDetailAPIView` (`views.py:1805`)

### POST /api/live/{id}/payments/orders/{order_id}/mark-paid/
- **File**: `LivePaymentOrderMarkPaidAPIView`
- **Notes**: called after external payment confirms (webhook or manual)

---

## 24. Live — Products

❌ All mobile-unused (creator-side product binding).

### GET / POST /api/live/{id}/products/manage/
- **File**: `LiveStreamProductManageListCreateAPIView`
- **Request**: `product_id` (from store), `position`

### GET / PATCH / DELETE /api/live/{id}/products/manage/{binding_id}/
- **File**: `LiveStreamProductManageDetailAPIView`

---

## 25. Gift System

### GET /api/gifts/
- **File**: `gift_urls.py` → `GiftListAPIView` (`gift_views.py:26`)
- **Auth**: none
- **Mobile usage**: ✅ used
- **Response (200)**: `[GiftSerializer]`: `{id, code, name, emoji, coin_cost (alias for points_price), points_price, icon_url, animation_url, is_active, sort_order}`

### Gift Sending (covered above)
- Video: `POST /api/public/videos/{id}/gifts/send/`
- Drama: `POST /api/dramas/{id}/gifts/send/`
- Live: `POST /api/live/{id}/gifts/send/` (only one with WebSocket broadcast)

### Gift Send Flow
1. Validate request (amount + payment_method OR gift_id/quantity)
2. Call `GiftService.send_*_gift()`:
   - Debit sender wallet (points or credits)
   - Credit receiver wallet (same currency)
   - Create `GiftTransaction(sender, receiver, target_type, target_id, gift_id?, quantity, amount, payment_method, points_amount, credits_amount, created_at)`
3. Return `sender_balance` and `receiver_balance` post-transaction
4. **Live only**: create `LiveChatMessage(type=TYPE_GIFT)` + broadcast to `live_chat_{stream_id}` group

### GiftTransaction model fields
- `id`, `sender_id`, `receiver_id`
- `stream_id?`, `video_id?`, `drama_series_id?` (FK by target type)
- `target_type` ∈ {TARGET_VIDEO, TARGET_LIVE_STREAM, TARGET_DRAMA_SERIES}
- `target_id`
- `gift_id?` (fixed-gift mode only)
- `gift_name_snapshot`, `points_price_snapshot` (fixed gifts)
- `quantity` (fixed) or `1` (amount mode)
- `amount` (amount mode)
- `payment_method` ∈ {meow_points, meow_credit}
- `points_amount`, `credits_amount` (one zero, one non-zero depending on payment_method)
- `total_points` (legacy?)
- `created_at`

### ContentGiftSendResponse shape
```json
{
  "video_id": 123,
  "receiver_id": 456,
  "amount": 100,
  "payment_method": "meow_points",
  "points_charged": 100,
  "credits_charged": 0,
  "sender_balance": 5000,
  "receiver_balance": 2300,
  "gift_transaction_id": 789
}
```

---

## 26. Shop — Buyer (catalog)

### GET /api/shop/banners/
- **File**: `shop_urls.py` → `ShopBannerListAPIView` (`views.py:1327`)
- **Auth**: none
- **Mobile usage**: ✅ used (home carousel)
- **Response (200)**: array (no pagination) of `{id, title, description, cover_image_url, action_type, action_target, sort_order, is_active}`

### GET /api/shop/categories/
- **File**: `ShopCategoryListAPIView` (`views.py:1334`)
- **Auth**: none
- **Mobile usage**: ✅ used
- **Response (200)**: array starting with synthetic `{id: 0, name: "All", slug: "all"}` then `[{id, name, slug}]`
- **Notes**: **distinct from `/api/public/categories/`** — needs reconciliation

### GET /api/shop/products/
- **File**: `ShopProductListAPIView` (`views.py:1346`)
- **Auth**: none
- **Mobile usage**: ✅ used
- **Request (query)**: `category?` (slug, default 'all'), `q?` (search on title/desc/slug), `page` (20/page), also `?seller={userId}` for filtering by seller
- **Response (200)**: paginated `{count, page, page_size, results: [{id, title, description, price_amount, price_currency, cover_image, meow_points_price?, meow_credit_price?, stock_quantity, store: {id, name, slug, owner: {id, display_name}}, category: {id, name, slug}}]}`

### GET /api/shop/products/{id}/
- **File**: `ShopProductDetailAPIView` (`views.py:1367`)
- **Auth**: none
- **Mobile usage**: ✅ used (PDP)
- **Response (200)**: same as list + `created_at`, `updated_at`, full store object, full category object, +5 computed fields

---

## 27. Shop — Cart

**Cart is DB-backed (SavedProduct model), NOT session-only** (despite mobile analysis suggesting "skeleton").

### GET /api/cart/items/
- **File**: `cart_urls.py` → `CartItemListCreateAPIView` (`views.py:1388`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Request (query)**: `page` (20/page)
- **Response (200)**: paginated `{id, product: {...}, user_id, created_at}`

### POST /api/cart/items/
- **Same view**
- **Request**: `{product_id}`
- **Response (201)**: cart item
- **Side effects**: `get_or_create()` SavedProduct — idempotent

### DELETE /api/cart/items/{id}/
- **File**: `CartItemDeleteAPIView` (`views.py:1421`)
- **Response (204)**: no content

### GET /api/cart/count/
- **File**: `CartCountAPIView` (`views.py:1430`)
- **Mobile usage**: ✅ used (cart badge)
- **Response (200)**: `{count: <int>}`

---

## 28. Shop — Product Orders

### POST /api/product-orders/
- **File**: `product_order_urls.py` → `ProductOrderListCreateAPIView` (`views.py:514`)
- **Auth**: required
- **Mobile usage**: ✅ used (checkout)
- **Request**: `{product_id, quantity, shipping_address_id, payment_asset: "thb_ltt"|"meow_points"|"meow_credit"}`
- **Response (201)**: `ProductOrderDetailSerializer` — `{order_no, status: "pending_payment", payment_method, pay_to_address, expected_amount, qr_payload, qr_text, expires_at, +30 more}`
- **Side effects**: creates ProductOrder → `ProductOrderService.create_order_with_asset()` → allocates stock → creates PaymentOrder → emits order event
- **Errors**: 503 (daemon unavailable), 400 (validation)

### GET /api/product-orders/
- **Same view**
- **Mobile usage**: ✅ used
- **Request (query)**: `status?`
- **Response (200)**: array (no pagination)

### GET /api/product-orders/{order_no}/
- **File**: `ProductOrderDetailAPIView` (`views.py:553`)
- **Mobile usage**: ✅ used

### POST /api/product-orders/{order_no}/cancel/
- **File**: `ProductOrderCancelAPIView` (`views.py:566`)
- **Auth**: buyer only
- **Mobile usage**: ✅ used
- **Request**: `{reason?: string}`
- **Side effects**: status → CANCELLED; release stock; optional refund
- **Notes**: allowed in PENDING_PAYMENT, PAID, SHIPPING; 400 if invalid state

### GET /api/product-orders/{order_no}/tracking/
- **File**: `ProductOrderTrackingAPIView` (`views.py:590`)
- **Mobile usage**: ✅ used
- **Response (200)**: `{order_no, tracking_number, carrier, tracking_url, shipment_status, estimated_delivery, last_update}`

### POST /api/product-orders/{order_no}/mark-paid/
- **File**: `ProductOrderMarkPaidAPIView` (`views.py:750`)
- **Auth**: 🛠 IsStaffOrSuperuser
- **Mobile usage**: 🛠 admin-only

### POST /api/product-orders/{order_no}/confirm-received/
- **File**: `ProductOrderConfirmReceivedAPIView` (`views.py:788`)
- **Auth**: buyer
- **Mobile usage**: ✅ used
- **Side effects**: SHIPPING → COMPLETED; may trigger seller payout

### GET /api/product-orders/{order_no}/tx-hint/
- **File**: `ProductOrderTxHintAPIView` (`views.py:654`)
- **Auth**: required
- **Mobile usage**: ✅ used (LBC payment verification info)
- **Response (200)**: `{order_no, payment_asset, expected_amount_lbc, pay_to_address, blockchain_network, currency, required_confirmations, +5 more}`

### POST / GET /api/product-orders/{order_no}/refund-requests/
- **File**: `ProductRefundRequestListCreateAPIView` (`views.py:844`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Request (POST)**: `{reason, requested_amount?}`
- **Response (201)**: `{id, product_order_id, status: "requested", reason, requested_amount, currency, created_at, admin_note?, resolved_at?}`
- **Notes**: only one active refund per order (REQUESTED or APPROVED); 400 if invalid state

---

## 29. Shop — QR Resolution

### POST /api/payment-qr/resolve/
- **File**: `product_order_urls.py` → `PaymentQRResolveAPIView` (`views.py:676`)
- **Auth**: none
- **Mobile usage**: ✅ used (QR scan flow)
- **Request**: `{qr_payload: object or json_string}`
- **Response (200)**: `{order_no, product_title, product_image, price, currency, seller_name, payment_asset, status, expires_at}`
- **Errors**: 404 if invalid/expired

---

## 30. Store — Seller Management

### GET /api/store/me/
- **File**: `store_urls.py` → `SellerStoreMeAPIView` (`views.py:1185`)
- **Auth**: required
- **Mobile usage**: ✅ used (Seller Studio)
- **Response (200)**: `{id, slug, name, description, owner: {id, display_name}, is_active, created_at, updated_at}`
- **Notes**: 404 if user is not seller

### POST /api/store/me/
- **Same view**
- **Mobile usage**: ✅ used
- **Request**: `{slug, name, description}`
- **Side effects**: creates SellerStore; requires prior APPROVED SellerApplication
- **Errors**: 409 (store exists), 403 (no approved application)

### PATCH /api/store/me/
- **Same view**
- **Request**: `{name?, description?, is_active?}`
- **Notes**: slug not editable

### GET /api/store/me/products/
- **File**: `SellerStoreMeProductListCreateAPIView` (`views.py:1234`)
- **Mobile usage**: ✅ used
- **Response (200)**: array (no pagination) of ProductSerializer — includes draft + active

### POST /api/store/me/products/
- **Same view**
- **Mobile usage**: ✅ used
- **Request** (multipart): `{title, description?, cover_image?: file, price_amount, price_currency, meow_points_price?, meow_credit_price?, stock_quantity, status: "draft"|"active"}`
- **Response (201)**: ProductSerializer

### GET / PATCH / DELETE /api/store/me/products/{pk}/
- **File**: `SellerStoreMeProductDetailAPIView` (`views.py:1256`)
- **DELETE**: ❌ mobile-unused; hard delete cascades; recommend status change instead

---

## 31. Store — Seller Applications

### POST /api/seller-applications/
- **File**: `seller_application_urls.py` → `SellerApplicationCreateAPIView` (`views.py:1109`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Request**: `{reason?, tax_id?, business_name?}`
- **Response (201)**: `{id, user_id, status: "pending", reason, submitted_at, reviewed_at?, reviewed_by?, rejection_reason?}`
- **Errors**: 409 if user already has store or pending application

### GET /api/seller-applications/me/
- **File**: `SellerApplicationMeAPIView` (`views.py:1124`)
- **Mobile usage**: ✅ used
- **Response (200)**: latest application (404 if none)

---

## 32. Store — Seller Orders (creator-facing)

### GET / POST /api/creator/shop/products/
- **File**: `creator_shop_urls.py` → `SellerStoreMeProductListCreateAPIView`
- **Notes**: alternate mount of seller product list, same view as `/api/store/me/products/`

### GET / PATCH / DELETE /api/creator/shop/products/{pk}/
- Same view as `/api/store/me/products/{pk}/`

### GET /api/creator/shop/orders/
- **File**: `SellerProductOrderListAPIView` (`views.py:618`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Request (query)**: `status?`
- **Response (200)**: array (no pagination) of `SellerProductOrderListSerializer`: `{order_no, status, product_title_snapshot, buyer: {id, display_name}, total_amount, currency, paid_at, shipped_at, completed_at}`
- **Notes**: filters by `seller_store__owner=user`

### GET /api/creator/shop/orders/{order_no}/
- **File**: `SellerProductOrderDetailAPIView` (`views.py:642`)
- **Mobile usage**: ✅ used
- **Response (200)**: full ProductOrderDetailSerializer

### POST /api/creator/shop/orders/{order_no}/ship/
- **File**: `SellerProductOrderShipAPIView` (`views.py:762`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Request**: `{carrier, tracking_number, tracking_url?, shipped_note?}`
- **Side effects**: creates ProductShipment; PAID → SHIPPING
- **Notes**: carrier ∈ {'FedEx', 'UPS', 'DHL', 'Other'}

---

## 33. Shipping Addresses

### GET /api/shipping-addresses/
- **File**: `shipping_urls.py` → `ShippingAddressListCreateAPIView` (`views.py:481`)
- **Auth**: required
- **Mobile usage**: ✅ used (checkout)
- **Response (200)**: array (no pagination) of `MobileShippingAddressSerializer`: `{id, user_id, name, phone, address, city, state, postal_code, country, is_default, created_at, updated_at}`
- **Notes**: **Field naming differs from `/api/account/shipping-addresses/`** — uses `name`/`address` vs `full_name`/`street_address`. Reconcile in new platform.

### POST /api/shipping-addresses/
- **Same view**
- **Request**: same shape (`is_default?`)

### GET / PATCH / DELETE /api/shipping-addresses/{id}/
- **File**: `ShippingAddressDetailAPIView` (`views.py:495`)
- **DELETE**: ❌ mobile-unused; auto-promotes next default if deleted was default

---

## 34. Membership — Plans / Orders / Me

### GET /api/membership/plans/
- **File**: `membership_urls.py` → `MembershipPlanListAPIView` (`views.py:3169`)
- **Auth**: none
- **Mobile usage**: ✅ used
- **Response (200)**: array of `{id, code, name, description, price_lbc, price_meow_points?, price_meow_credit?, duration_days, benefits, is_active, sort_order}`

### POST /api/membership/orders/
- **File**: `MembershipOrderCreateAPIView` (`views.py:3544`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Request**: `{plan_id, payment_asset?: "thb_ltt"|"meow_points"|"meow_credit"}` (default `thb_ltt`)
- **Response (201 or 200)**: `MembershipOrderSerializer`: `{order_no, plan: {id, code, name}, payment_asset, expected_amount_lbc, pay_to_address, qr_payload, qr_text, payment_uri, status, expires_at, paid_at, confirmations, txid, reused?: boolean}`
- **Notes**: **may reuse** unpaid order for same user/plan (returns 200 + `reused: true`); new order returns 201

### GET /api/membership/orders/
- **Same view**
- **Mobile usage**: ✅ used
- **Response (200)**: array (no pagination), ordered by `-created_at`

### GET /api/membership/orders/{order_no}/
- **File**: `MembershipOrderDetailAPIView` (`views.py:3582`)
- **Mobile usage**: ✅ used

### GET /api/membership/orders/{order_no}/tx-hint/
- **File**: `MembershipOrderTxHintAPIView` (`views.py:3597`)
- **Mobile usage**: ✅ used
- **Response (200)**: `{order_no, blockchain, network, token_symbol, expected_amount_lbc, pay_to_address, required_confirmations, currency}`

### POST /api/membership/orders/{order_no}/verify-now/
- **File**: `MembershipOrderVerifyNowAPIView` (`views.py:3628`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Request**: `{txid}`
- **Side effects**: calls LBC daemon verify; transitions to PAID; grants membership

### GET /api/membership/me/
- **File**: `MembershipMeAPIView` (`views.py:3801`)
- **Mobile usage**: ✅ used
- **Response (200)**: `MyMembershipSerializer`: `{user_id, plan: {id, code, name}, status: "active"|"expired"|"cancelled", starts_at, ends_at, is_expired, days_remaining, renewal_info?}` or `{}` if none active

---

## 35. Membership — Manual LBC Verification (6-step)

### GET /api/membership/manual/payment-info/
- **File**: `ManualMembershipPaymentInfoAPIView` (`views.py:3178`)
- **Auth**: required
- **Mobile usage**: ✅ used (step 1)
- **Request (query)**: `plan_code`, `payment_asset=thb_ltt`
- **Response (200)**: `{plan_code, plan_name, expected_amount_lbc, currency, pay_to_address, required_confirmations, notice}`
- **Notes**: **does NOT create order** — user must call `/orders/` separately.

### GET /api/membership/manual/tx-hints/
- **File**: `ManualMembershipTxHintListAPIView` (`views.py:3219`)
- **Auth**: required
- **Mobile usage**: ✅ used
- **Response (200)**: array of `ManualMembershipPaymentHintSerializer`: `{id, user_id, plan: {code, name}, txid, status: "pending"|"submitted"|"verified", created_at, updated_at}` (max 50, sorted `-created_at`)

### POST /api/membership/manual/tx-hints/
- **Same view**
- **Mobile usage**: ✅ used (step 2)
- **Request**: `{plan_code, txid, payment_asset: "thb_ltt"}`
- **Response (201)**: hint record created
- **Errors**: 409 if duplicate txid

### POST /api/membership/manual/tx-hints/{pk}/verify-now/
- **File**: `ManualMembershipTxHintVerifyNowAPIView` (`views.py:3410`)
- **Auth**: required
- **Mobile usage**: ⚠️ unclear (likely admin/support)
- **Request**: none (or `{txid}` resubmit)
- **Side effects**: calls LBC daemon; grants membership if confirmed
- **Notes**: step 4-5; idempotent

---

## 36. Membership — Billing / Subscription (recurring)

⚠️ Mobile usage unclear for this entire surface — recurring billing is a separate path from one-shot membership orders.

### GET /api/billing/plans/
- **File**: `billing_urls.py` → `BillingPlanListAPIView` (`views.py:3110`)
- **Auth**: none

### POST /api/billing/subscriptions/
- **File**: `BillingSubscriptionCreateAPIView` (`views.py:3119`)
- **Auth**: required
- **Request**: `{plan_id}`
- **Response (201)**: `{id, user_id, plan: {id, name, price_amount}, status: "active", auto_renew, created_at, subscription_end}`

### GET /api/billing/subscriptions/me/
- **File**: `BillingMySubscriptionAPIView` (`views.py:3137`)
- **Response (200)**: BillingSubscriptionSerializer or null

### POST /api/billing/subscriptions/{pk}/cancel/
- **File**: `BillingSubscriptionCancelAPIView` (`views.py:3151`)
- **Side effects**: CANCELLED; `auto_renew=false`

---

## 37. Admin — Seller Management

🛠 All admin-only.

### GET /api/admin/seller-applications/
- **File**: `admin_urls.py` → `AdminSellerApplicationListAPIView` (`views.py:1135`)
- **Request (query)**: `status?` ∈ pending|approved|rejected

### POST /api/admin/seller-applications/{pk}/approve/
- **File**: `AdminSellerApplicationApproveAPIView` (`views.py:1147`)
- **Side effects**: APPROVED + creates SellerStore + grants seller role

### POST /api/admin/seller-applications/{pk}/reject/
- **File**: `AdminSellerApplicationRejectAPIView` (`views.py:1163`)
- **Request**: `{rejection_reason}`

---

## 38. Admin — User Management

🛠 Admin-only.

### GET /api/admin/users/
- **File**: `AdminUserListAPIView` (`views.py:1027`)

### GET /api/admin/users/{pk}/
- **File**: `AdminUserDetailAPIView` (`views.py:1033`)

### POST /api/admin/users/{pk}/activate/ + /deactivate/
- **File**: `AdminUserActivationAPIView` (`views.py:1039`)
- **Notes**: same view, `active=True/False` parameter; deactivate invalidates sessions

---

## 39. Admin — Video Moderation

🛠 Admin-only.

### GET /api/admin/videos/
- **File**: `AdminVideoListAPIView` (`views.py:1052`)

### GET /api/admin/videos/{pk}/
- **File**: `AdminVideoDetailAPIView` (`views.py:1098`)

---

## 40. Admin — Refund Management

🛠 Admin-only.

### GET /api/admin/refund-requests/
- **File**: `product_order_urls.py` → `AdminRefundRequestListAPIView` (`views.py:897`)

### POST /api/admin/refund-requests/{pk}/approve/
- **File**: `AdminRefundRequestApproveAPIView` (`views.py:904`)
- **Request**: `{admin_note?}`
- **Side effects**: REQUESTED → APPROVED

### POST /api/admin/refund-requests/{pk}/reject/
- **File**: `AdminRefundRequestRejectAPIView` (`views.py:921`)
- **Side effects**: REQUESTED|APPROVED → REJECTED; `resolved_at = now`

### POST /api/admin/refund-requests/{pk}/mark-refunded/
- **File**: `AdminRefundRequestMarkRefundedAPIView` (`views.py:938`)
- **Side effects**: REQUESTED|APPROVED → REFUNDED; **performs actual refund** (wallet credit or asset return); updates SellerPayout to FAILED if pending; idempotent

---

## 41. Admin — Order Settlement

### POST /api/admin/product-orders/{order_no}/mark-settled/
- **File**: `product_order_urls.py` → `AdminProductOrderMarkSettledAPIView` (`views.py:1005`)
- **Auth**: 🛠
- **Side effects**: COMPLETED → SETTLED; final accounting; refunds become manual only

---

## 42. Channel (deprecated)

🚫 **`channel_urls.py` is entirely deprecated. New platform should NOT implement.** Mobile uses `/api/public/users/{id}/follow/`. **Web still uses `/api/channels/{id}/subscribe/`** — must migrate before cutover.

### POST /api/channels/{id}/subscribe/
- **File**: `channel_urls.py` → `ChannelSubscriptionAPIView` (`views.py:2850`)
- **Auth**: required
- **Mobile usage**: ❌ mobile-unused
- **Response (200)**: `{ok: true, is_subscribed: true}`

### DELETE /api/channels/{id}/subscribe/
- **Same view**; unsubscribe

---

## 43. State Machine Reference

### Product Order

```
PENDING_PAYMENT ─(payment)─→ PAID ─(seller ships)─→ SHIPPING ─(buyer confirms)─→ COMPLETED ─(admin)─→ SETTLED (terminal)
       ↓                       ↓                       ↓
       └──────────────── (buyer cancels) ──────────→ CANCELLED (terminal except from SETTLED)
```

| Transition | Endpoint | Actor |
|---|---|---|
| → PENDING_PAYMENT | `POST /api/product-orders/` | Buyer |
| PENDING_PAYMENT → PAID | `POST /api/product-orders/{order_no}/mark-paid/` | Admin (manual) |
| PENDING_PAYMENT → PAID | LBC verify-now / instant meow_points/credit | Buyer |
| PAID → SHIPPING | `POST /api/creator/shop/orders/{order_no}/ship/` | Seller |
| SHIPPING → COMPLETED | `POST /api/product-orders/{order_no}/confirm-received/` | Buyer |
| COMPLETED → SETTLED | `POST /api/admin/product-orders/{order_no}/mark-settled/` | Admin |
| * → CANCELLED | `POST /api/product-orders/{order_no}/cancel/` | Buyer (not from SETTLED) |

### Refund Request

```
REQUESTED ─(admin)─→ APPROVED ─(admin marks)─→ REFUNDED (terminal)
    ↓                   ↓
    └────(admin)────→ REJECTED (terminal)
```

### Live Stream

```
IDLE ─(prepare)─→ READY ─(start)─→ LIVE ─(end)─→ ENDED (terminal)
                                            ↓
                                          FAILED (terminal)
```

- Only IDLE/READY → LIVE allowed
- ENDED is terminal; cannot restart

### KYC

```
NOT_SUBMITTED ─(POST /me/)─→ PENDING ─(admin approves)─→ APPROVED
                                ↓
                              REJECTED ─(reapply via POST /me/)─→ PENDING
```

- POST `/api/kyc/documents/` on APPROVED profile **resets to PENDING**

### Seller Application

```
PENDING ─(admin approve)─→ APPROVED ─(creates SellerStore)
   ↓
 REJECTED ─(user reapply)─→ PENDING
```

### Membership Manual LBC (6-step)

```
Step 1: GET  /api/membership/manual/payment-info/?plan_code=X
Step 2: POST /api/membership/manual/tx-hints/ {plan_code, txid}        → status=submitted
Step 3-4: [staff verification or auto-verify]                          → dry_run_verified / pending_confirmation
Step 5: POST /api/membership/manual/tx-hints/{pk}/verify-now/          → status=verified
Step 6: UserMembership created with ACTIVE status
```

### Drama Episode Unlock (4 methods)
- **free** (`episode.is_free=True`): `can_watch=true` immediately, no unlock call
- **meow_points** (`unlock_type=MEOW_POINTS`): `POST /api/dramas/episodes/{id}/unlock/` with `payment_method=meow_points` charges `episode.meow_points_price`
- **meow_credit** (`unlock_type=MEOW_CREDIT`): same endpoint, `payment_method=meow_credit` charges `episode.meow_credit_price`
- **membership** (`unlock_type=MEMBERSHIP`): **no unlock endpoint**; `DramaAccessService.has_active_membership()` grants access directly (checked in `DramaEpisodeListAPIView`)

---

## 44. Gaps, Observations, and Inconsistencies

### High-priority gotchas (new platform must address)

1. **Daily login reward is baked into `POST /api/auth/login/` response**. Mobile expects this. New platform should preserve the reward grant but make the side effect explicit (separate endpoint or asynchronous event). Failure in grant must not fail auth.

2. **Wallet auto-creation on first access** is silent (warning log only). Should be explicit in new platform — create wallets at user registration.

3. **No concurrency protection on wallet writes** — `SELECT FOR UPDATE` is missing. New platform must add this (per ADR-0004).

4. **No `idempotency_key UNIQUE` constraint on ledger entries** — relies on `created_at` for dedup. New platform must add explicit `idempotency_key`.

5. **`/api/account/shipping-addresses/` vs `/api/shipping-addresses/`** are two different endpoints with **different field naming** (`full_name`/`street_address` vs `name`/`address`). Mobile uses the latter. Reconcile in new platform.

6. **`channel_urls.py` web/mobile conflict**: mobile migrated to `/api/public/users/{id}/follow/`; **web still uses `/api/channels/{id}/subscribe/`**. Web must migrate before cutover.

7. **Shop categories vs Public categories** are two different endpoints. Mobile shop UI uses `/api/shop/categories/` which prepends a synthetic "All". Reconcile or document.

8. **Three different pagination styles**. New platform should standardize on cursor.

9. **Currency code inconsistency**: `MP`, `MC`, `USD`, `LBC`, `thb_ltt`, `meow_points`, `meow_credit`. New platform should pick one schema and migrate.

10. **Money as string vs int inconsistency**: wallet endpoints return string ("1234.56"), aggregate `/api/user-balance/` returns int. Standardize on string Decimal.

11. **`coins` and top-level `currency` aliases** in `/api/user-balance/balance/` are legacy. New platform should drop.

12. **Live Gift is the only gift endpoint that broadcasts via WebSocket**. Video and drama gifts are silent. Document explicitly.

13. **`POST /api/meow-points/orders/{order_no}/` GET auto-credits paid purchases as a side effect of GET**. This is a hidden side effect on read. New platform should split read/write.

14. **`mobile-unused` but kept-for-web**: `POST /api/account/change-password/` is unused by mobile but may be needed for web. Keep in new platform but flag.

15. **`POST /api/live/{id}/start/` query `skip_ant_media`** is a debug bypass only when `DEBUG=true` or `ALLOW_LIVE_START_BYPASS=true`. Document; consider removing.

16. **Two different fixed-gift modes**: amount-based (preferred) and gift_id/quantity (legacy with 2-second dedup window). New platform should drop legacy.

### Mobile expects but not found / unclear

- **Live payment confirmation flow** — `/mark-paid/` exists but webhook/external integration not documented.
- **Drama watch-config equivalent** — Live has `/watch-config/`; drama doesn't. Mobile fetches `episode.hls_url` directly from episode detail.
- **Drama unlock via membership** — code shows 4 methods but unlock endpoint handles only points/credit. Membership unlock is implicit via `DramaAccessService.has_active_membership()` check at list time.
- **Seller payout history** — no explicit endpoint; payout info embedded in refund/order views.

### Webhook endpoints
- **None found in `apps/accounts/`**. All payment verification is polling-based (`verify-now`).
- Webhooks (if any) likely handled in separate service or app.

### Concurrency / race risks observed
1. **Like / comment count**: `F()` updates with `refresh_from_db()` mitigate but brief stale window exists.
2. **Gift wallet debit race**: if `GiftService` debits wallet but Ant Media call fails (live), wallet debited without GiftTransaction visible.
3. **Live state machine race**: concurrent `start/` calls could both see IDLE and both start Ant Media.
4. **Viewer count dedup race**: `cache.add()` 60-sec dedup; cache miss on failover could double-count.
5. **Drama unlock dedup**: concurrent unlock could double-charge if DB commit delayed.
6. **Chat message broadcast race**: REST POST writes DB then `group_send()`; if channel layer fails, message persists but no broadcast.

---

## 45. Mobile-Unused Endpoint Index (for new-platform exclusion)

Endpoints confirmed **not used by mobile**. New platform's V1 (mobile-only cutover) does **not** need to implement these.

### Fully deprecated / do not implement

| Endpoint | File | Reason |
|---|---|---|
| POST /api/channels/{id}/subscribe/ | channel_urls.py | Web uses, but migrate before cutover |
| DELETE /api/channels/{id}/subscribe/ | channel_urls.py | Same |
| POST /api/creators/{creator_id}/follow/ | creators_urls.py | Legacy, replaced by `/api/public/users/{id}/follow/` |
| DELETE /api/creators/{creator_id}/follow/ | creators_urls.py | Same |
| POST /api/wallet-prototype/pay-order/ | wallet_prototype_urls.py | Prototype |
| POST /api/wallet-prototype/pay-product-order/ | wallet_prototype_urls.py | Prototype |
| `channel_urls`, `creator_live_urls`, `live_url`, `channel_url`, `profile_url`, `web_url` fields (serializer-level) | various | Three-frontend confirmed unused |

### Mobile-unused but may be needed elsewhere (keep but flag)

| Endpoint | Reason |
|---|---|
| POST /api/account/change-password/ | Web may use |
| GET /api/account/payment-orders/ | Internal/admin use |
| POST /api/meow-points/orders/{order_no}/tx-hint/ | Internal tx flow |
| POST /api/meow-credit/recharges/{order_no}/tx-hint/ | Internal tx flow |
| GET/POST /api/meow-credit/redeems/ | Backend workflow |
| All `/api/admin/*` endpoints | Staff operations |
| GET /api/live/health/ | Diagnostic |
| All `/api/live/{id}/payment-methods/manage/*` | Creator setup |
| All `/api/live/{id}/products/manage/*` | Creator setup |
| All `/api/creators/dramas/*` (creator drama management) | Desktop/web only |
| `/api/creators/videos/`, `/api/creators/live/` | Creator dashboard |
| DELETE on `/api/store/me/products/{pk}/` and `/api/creator/shop/products/{pk}/` | Hard delete; use status change |
| DELETE /api/shipping-addresses/{id}/ | Cleanup-only |

### V1 priority categorization (mobile-only cutover)

**V1 must-have**:
- §2 Auth, §3 Account/Profile, §4 KYC, §5 Library, §6-7 Wallets (MeowPoints, MeowCredit), §8 Aggregate Balance, §11-13 Public Users/Creators/Store, §15 Video Public Catalog, §17 Drama Public, §19 Live Public, §21-22 Live Chat REST+WS, §25 Gift system, §26-33 Shop+Cart+Orders+Store+Shipping, §34-35 Membership

**V1 should-have** (mobile uses but lower priority):
- §16 Video Creator (if mobile creator features needed)
- §28 Order tracking, refund flow

**V1 deferred**:
- §18 Drama Creator Management
- §20 Live Creator/Broadcaster
- §23-24 Live payment methods + products
- §36 Recurring billing
- §37-41 Admin

**V1 NOT implementing**:
- §9 Wallet Prototype
- §14 Legacy Creators (`/api/creators/{id}/follow/`)
- §42 Channel
- All `channel_urls`, `creator_live_urls`, etc. serializer fields

---

**End of MOBILE_API_CONTRACT_FULL**

This document supersedes the original `MOBILE_API_CONTRACT.md` as the comprehensive reference for the django-auth-core backend API surface. Use it as input when designing the new platform's contracts under `brandable-content-platform/docs/contracts/`.
