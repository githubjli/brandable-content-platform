# Identity Contract

Covers: Auth, Account, Profile, KYC, CreatorProfile, Follow/Subscribe, Public Users, Public Creators.

**App**: `apps/identity/`
**Legacy reference**: `MOBILE_API_CONTRACT_FULL.md` §2-4, §11-12

---

## 1. Authentication

### POST /api/v1/auth/register 🟢 V1
**Auth**: none
**Idempotency**: yes (`Idempotency-Key` required)

#### Request
```json
{
  "email": "user@example.com",
  "password": "<min 8 chars>",
  "display_name": "Jane Doe",
  "first_name": "Jane",
  "last_name": "Doe"
}
```

#### Response 201
```json
{
  "user": {
    "id": "<uuid>",
    "email": "user@example.com",
    "display_name": "Jane Doe",
    "avatar_url": null,
    "is_creator": false,
    "is_admin": false,
    "created_at": "2026-06-04T10:00:00Z"
  },
  "tokens": {
    "access": "<jwt>",
    "refresh": "<jwt>",
    "expires_at": "2026-06-04T10:15:00Z"
  }
}
```

#### Errors
- 400 `VALIDATION_INVALID_EMAIL`
- 400 `VALIDATION_PASSWORD_TOO_WEAK`
- 409 `AUTH_EMAIL_ALREADY_EXISTS`

#### Side effects
- Creates User (email normalized: lowercase + stripped)
- Creates PointWallet + CreditWallet **explicitly** (not lazy)
- Writes `OutboxEvent`: `identity.UserRegistered`
- Writes `AuditLog`

#### Diff from legacy
- Email auto-lowercased + stripped (was case-sensitive)
- Wallets created explicitly (legacy lazy-created on first access with warning)
- Response shape: tokens nested under `tokens` (was at top level)
- Removed: `linked_wallet_id`, `primary_user_address`, `wallet_link_status`, `linked_at` (blockchain prototype residue)

---

### POST /api/v1/auth/login 🟢 V1
**Auth**: none
**Idempotency**: no (intentionally; replay protection via JWT `jti`)

#### Request
```json
{
  "email": "user@example.com",
  "password": "<password>"
}
```

#### Response 200
```json
{
  "user": { /* same shape as register */ },
  "tokens": {
    "access": "<jwt>",
    "refresh": "<jwt>",
    "expires_at": "2026-06-04T10:15:00Z"
  },
  "session": {
    "id": "<uuid>",
    "device_label": null
  }
}
```

#### Errors
- 401 `AUTH_INVALID_CREDENTIALS`
- 403 `AUTH_ACCOUNT_DEACTIVATED`
- 429 `RATE_LIMIT_EXCEEDED`

#### Side effects
- Creates `UserSession` record (tracks refresh token)
- Writes `OutboxEvent`: `identity.UserLoggedIn`
- **Daily login reward**: emits `OutboxEvent`: `economy.DailyLoginRewardClaimRequested`. Async handler grants reward to wallet via `EconomyService`. Reward outcome is **NOT** returned in this response.

#### Diff from legacy
- **`daily_login_reward` removed from response.** Legacy baked it into login synchronously; new platform makes it async via Outbox. Mobile must call `GET /api/v1/economy/wallets/me/point` after login to see updated balance.
- Mobile also has explicit claim endpoint: `POST /api/v1/economy/daily-rewards/claim` (see economy.md)
- New: `session` object returned for force-logout management

⚠️ **Breaking for mobile**: must update login response parsing.

---

### POST /api/v1/auth/refresh 🟢 V1
**Auth**: refresh token in body
**Idempotency**: no (refresh tokens rotate)

#### Request
```json
{
  "refresh": "<refresh_jwt>"
}
```

#### Response 200
```json
{
  "tokens": {
    "access": "<jwt>",
    "refresh": "<new_jwt>",
    "expires_at": "..."
  }
}
```

#### Errors
- 401 `AUTH_REFRESH_INVALID`
- 401 `AUTH_REFRESH_EXPIRED`
- 401 `AUTH_SESSION_REVOKED`

#### Side effects
- Rotates refresh token (old becomes invalid)
- Updates `UserSession.last_used_at`

---

### POST /api/v1/auth/logout 🟢 V1 (new)
**Auth**: required
**Idempotency**: yes

#### Request
```json
{ "refresh": "<refresh_jwt>" }
```

#### Response 204
No body.

#### Side effects
- Deletes `UserSession`
- Refresh token invalidated immediately
- Access token remains valid until natural `exp`

#### Diff from legacy
**New endpoint.** Legacy had no logout (client-side token discard only).

---

### POST /api/v1/auth/password/reset/request 🟢 V1 (new)
**Auth**: none
**Idempotency**: yes

#### Request
```json
{ "email": "user@example.com" }
```

#### Response 204
Always returns 204 even if email doesn't exist (prevents user enumeration).

#### Side effects
- If email exists: emits `OutboxEvent`: `identity.PasswordResetRequested` → NotificationService sends reset link

---

### POST /api/v1/auth/password/reset/confirm 🟢 V1 (new)
**Auth**: none
**Idempotency**: yes

#### Request
```json
{
  "reset_token": "<token from email>",
  "new_password": "<min 8 chars>"
}
```

#### Response 204

#### Errors
- 400 `AUTH_RESET_TOKEN_INVALID`
- 400 `AUTH_RESET_TOKEN_EXPIRED`
- 400 `VALIDATION_PASSWORD_TOO_WEAK`

#### Side effects
- Updates password
- Invalidates all `UserSession` for user (forces re-login)

---

### POST /api/v1/auth/password/change 🟢 V1
**Auth**: required
**Idempotency**: yes

#### Request
```json
{
  "current_password": "<current>",
  "new_password": "<new>"
}
```

#### Response 204

#### Errors
- 401 `AUTH_INVALID_CREDENTIALS`
- 400 `VALIDATION_PASSWORD_TOO_WEAK`

#### Side effects
- Updates password
- Optionally invalidates other sessions (controlled by `revoke_other_sessions=true` in request)

---

### GET /api/v1/auth/me 🟢 V1
**Auth**: required

#### Response 200
```json
{
  "user": {
    "id": "<uuid>",
    "email": "user@example.com",
    "display_name": "Jane Doe",
    "avatar_url": "https://...",
    "is_creator": false,
    "is_admin": false,
    "kyc_status": "approved",
    "created_at": "...",
    "session_id": "<uuid>"
  }
}
```

#### Diff from legacy
- Removed: `linked_wallet_id`, `primary_user_address`, `wallet_link_status`, `linked_at`
- Added: `kyc_status` (one of `not_submitted`, `pending`, `approved`, `rejected`) — derived from KycProfile

---

### GET /api/v1/auth/sessions 🟢 V1 (new)
**Auth**: required

#### Response 200
```json
{
  "results": [
    {
      "id": "<uuid>",
      "device_label": "iPhone 15",
      "ip_address": "1.2.3.4",
      "last_used_at": "...",
      "created_at": "...",
      "is_current": true
    }
  ]
}
```

#### Diff from legacy
**New.** Sessions list for force-logout UI.

---

### DELETE /api/v1/auth/sessions/{session_id} 🟢 V1 (new)
**Auth**: required

#### Response 204

#### Side effects
- Deletes session; invalidates its refresh token

---

## 2. Account & Profile

### GET /api/v1/account/profile 🟢 V1
**Auth**: required

#### Response 200
```json
{
  "id": "<uuid>",
  "email": "user@example.com",
  "display_name": "Jane Doe",
  "first_name": "Jane",
  "last_name": "Doe",
  "avatar_url": "https://...",
  "bio": "...",
  "is_creator": false,
  "is_seller": false,
  "is_admin": false,
  "follower_count": 0,
  "following_count": 0,
  "creator_profile": null,
  "stats": {
    "total_videos": 0,
    "total_dramas": 0,
    "total_likes_received": 0,
    "total_views_received": 0,
    "total_gifts_received_amount": "0.0000",
    "total_gifts_received_currency": "MP"
  },
  "created_at": "..."
}
```

If `is_creator=true`, `creator_profile` is an object (see CreatorProfile below).

#### Diff from legacy
- Stats consolidated under `stats`
- Counts renamed: `total_gifts` → `total_gifts_received_amount` + `_currency` (legacy had ambiguous semantics)
- `creator_profile` nested instead of flat `is_creator` boolean only

---

### PATCH /api/v1/account/profile 🟢 V1
**Auth**: required
**Idempotency**: yes
**Content-Type**: `multipart/form-data` (for avatar) or `application/json`

#### Request (any subset)
```json
{
  "display_name": "...",
  "first_name": "...",
  "last_name": "...",
  "bio": "...",
  "avatar": "<file>",
  "avatar_clear": true
}
```

#### Response 200
Same as GET.

#### Side effects
- Updates User
- Writes `OutboxEvent`: `identity.ProfileUpdated`

---

### GET /api/v1/account/preferences 🟢 V1
**Auth**: required

#### Response 200
```json
{
  "language": "en-US",
  "theme": "system",
  "timezone": "Asia/Bangkok",
  "notifications": {
    "email_enabled": true,
    "push_enabled": false
  }
}
```

#### Diff from legacy
- Added `notifications` object (legacy had no opt-in management)

---

### PATCH /api/v1/account/preferences 🟢 V1
Same shape, any subset.

---

## 3. Creator Profile

`CreatorProfile` is a 1:1 extension of User. Created when user is approved as creator.

### GET /api/v1/account/creator-profile 🟢 V1
**Auth**: required + `is_creator=true`

#### Response 200
```json
{
  "user_id": "<uuid>",
  "bio_extended": "Long-form creator bio",
  "categories": ["music", "gaming"],
  "social_links": {
    "twitter": "https://...",
    "youtube": "https://..."
  },
  "is_verified": false,
  "verified_at": null,
  "kyc_status": "approved",
  "created_at": "..."
}
```

### PATCH /api/v1/account/creator-profile 🟢 V1
Updates partial fields.

#### Diff from legacy
**Split from User in new platform.** Legacy had `is_creator` boolean + scattered creator fields on User. New platform isolates creator concerns to CreatorProfile.

---

## 4. KYC

State machine: `not_submitted` → `pending` → `approved` | `rejected`

### GET /api/v1/account/kyc 🟢 V1
**Auth**: required

#### Response 200
```json
{
  "status": "not_submitted",
  "full_name": null,
  "date_of_birth": null,
  "nationality": null,
  "id_type": null,
  "id_number": null,
  "id_expiry_date": null,
  "submitted_at": null,
  "reviewed_at": null,
  "reject_reason": null,
  "documents": {
    "id_front": null,
    "selfie": null
  }
}
```

When `status != not_submitted`, fields are populated.

### PUT /api/v1/account/kyc 🟢 V1
**Auth**: required
**Idempotency**: yes

#### Request
```json
{
  "full_name": "Jane Doe",
  "date_of_birth": "1990-01-01",
  "nationality": "TH",
  "id_type": "passport",
  "id_number": "ABC123456",
  "id_expiry_date": "2030-01-01"
}
```

#### Side effects
- Creates/updates KycProfile
- If previously approved, **resets to `pending`** + emits `OutboxEvent`: `identity.KycResubmitted`

---

### POST /api/v1/account/kyc/documents 🟢 V1
**Auth**: required
**Content-Type**: `multipart/form-data`

#### Request
```
document_type: "id_front" | "selfie"
image: <file>
```

#### Response 200
```json
{
  "document_type": "id_front",
  "image_url": "https://...",
  "uploaded_at": "..."
}
```

---

### POST /api/v1/account/kyc/submit 🟢 V1
**Auth**: required
**Idempotency**: yes

Finalizes submission. Validates required fields + documents present.

#### Response 200
KycProfile object (status = `pending`).

#### Errors
- 400 `KYC_REQUIRED_FIELDS_MISSING`
- 400 `KYC_REQUIRED_DOCUMENTS_MISSING`

#### Side effects
- Sets status = `pending`, `submitted_at = now`
- Emits `OutboxEvent`: `identity.KycSubmitted`

---

## 5. Follow / Subscribe

**Single canonical path**: `/api/v1/public/users/{user_id}/follow`. Legacy `/api/channels/{id}/subscribe/` and `/api/creators/{id}/follow/` are **NOT implemented** (see deprecated.md).

### POST /api/v1/public/users/{user_id}/follow 🟢 V1
**Auth**: required
**Idempotency**: yes (no-op if already following)

#### Response 200
```json
{
  "user_id": "<target uuid>",
  "is_following": true,
  "follower_count": 1234
}
```

#### Errors
- 422 `FOLLOW_SELF_FORBIDDEN`
- 404 `USER_NOT_FOUND`

#### Side effects
- Creates `Follow` record (was `ChannelSubscription`)
- Increments target's follower_count
- Emits `OutboxEvent`: `identity.UserFollowed`

#### Diff from legacy
- Removed alias fields: `subscriber_count`, `is_subscribed`, `viewer_is_following`, `viewer_is_subscribed` — use single `is_following` and `follower_count`

### DELETE /api/v1/public/users/{user_id}/follow 🟢 V1
Symmetric to POST.

---

## 6. Public Users

### GET /api/v1/public/users/{user_id} 🟢 V1
**Auth**: optional (richer response if authed)

#### Response 200
```json
{
  "id": "<uuid>",
  "display_name": "Jane Doe",
  "avatar_url": "...",
  "bio": "...",
  "is_creator": false,
  "creator_profile": null,
  "follower_count": 0,
  "following_count": 0,
  "viewer_context": {
    "is_following": false,
    "is_self": false
  },
  "created_at": "..."
}
```

If `is_creator=true`, `creator_profile` is populated (subset of CreatorProfile).

If auth missing, `viewer_context` is null.

#### Diff from legacy
- `email` removed from public response (was masked but exposed structurally)
- `viewer_context` consolidates is_following / is_self
- Removed: `subscriber_count` alias

---

### GET /api/v1/public/users/{user_id}/followers 🟡 V2
**Auth**: optional

Cursor-paginated list of users following target.

### GET /api/v1/public/users/{user_id}/following 🟡 V2
Cursor-paginated list of users target follows.

---

## 7. Public Creators

`/api/v1/public/creators/{id}` is a convenience view filtering to `is_creator=True` with extra creator-specific content lists. **Backed by same User table.**

### GET /api/v1/public/creators/{creator_id} 🟢 V1
Same as `/api/v1/public/users/{user_id}` but 404 if user is not a creator.

### GET /api/v1/public/creators/{creator_id}/videos 🟢 V1
Cursor-paginated public videos. Schema: see content-video.md.

### GET /api/v1/public/creators/{creator_id}/dramas 🟡 V2
Cursor-paginated public drama series.

### GET /api/v1/public/creators/{creator_id}/lives 🔵 V3
Cursor-paginated past/current live streams.

---

## 8. Outbox events emitted by Identity

| Event | When |
|---|---|
| `identity.UserRegistered` | After register success |
| `identity.UserLoggedIn` | After login success |
| `identity.PasswordResetRequested` | After reset request |
| `identity.PasswordChanged` | After change/reset confirm |
| `identity.ProfileUpdated` | After profile PATCH |
| `identity.KycSubmitted` | After KYC submit |
| `identity.KycApproved` | After admin approval |
| `identity.KycRejected` | After admin rejection |
| `identity.KycResubmitted` | After upload on approved profile |
| `identity.UserFollowed` | After follow POST |
| `identity.UserUnfollowed` | After follow DELETE |
| `identity.CreatorPromoted` | After SellerApplication approval (see commerce.md) |

All payloads carry `user_id` + `actor_id` + `occurred_at` + event-specific fields.
