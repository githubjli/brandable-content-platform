# Content — Drama Contract

Covers: drama series + episodes, 4 unlock methods, favorites, comments, shares, gifts, progress tracking, creator management.

**App**: `apps/content/drama/`
**Legacy reference**: `MOBILE_API_CONTRACT_FULL.md` §17-18
**Priority**: 🟡 V2 (mobile-critical but not V1 backend scope)

---

## 1. Public — Series

### GET /api/v1/content/drama/series 🟡 V2
**Auth**: optional
**Cursor-paginated**

#### Request (query)
```
?cursor=<>&limit=20
&category=<slug-or-uuid>
&ordering=-created_at|-view_count|-favorite_count
```

#### Response 200
```json
{
  "results": [
    {
      "id": "<uuid>",
      "title": "...",
      "description": "...",
      "cover_url": "...",
      "tags": ["romance", "comedy"],
      "category": {"id": "<uuid>", "name": "...", "slug": "..."},
      "owner": {"id": "<uuid>", "display_name": "...", "avatar_url": "...", "is_creator": true},
      "counts": {
        "total_episodes": 12,
        "free_episodes": 3,
        "locked_episodes": 9,
        "view": 12345,
        "favorite": 678,
        "comment": 42,
        "share": 12,
        "gift_amount": "100.0000",
        "gift_currency": "MP"
      },
      "viewer_context": {
        "is_favorited": false,
        "is_following_owner": false,
        "continue": {
          "episode_no": 3,
          "progress_seconds": 245
        }
      },
      "created_at": "..."
    }
  ],
  "cursor": {"next": "...", "prev": null}
}
```

#### Diff from legacy
- Owner/counts/viewer_context nested
- Removed: `channel_id`, `channel_name`, `subscriber_count`, `is_subscribed`, all `*_alias` fields
- `continue_episode_no` + `continue_progress_seconds` → `viewer_context.continue` object

---

### GET /api/v1/content/drama/series/{series_id} 🟡 V2
Single series with same shape.

---

### GET /api/v1/content/drama/series/{series_id}/interactions 🟡 V2
Mirrors video interactions endpoint. Returns counts + viewer_context.

---

### POST /api/v1/content/drama/series/{series_id}/view 🟡 V2
Track series-level view. Dedup per user/IP per minute.

### POST /api/v1/content/drama/series/{series_id}/share 🟡 V2
Same as video share.

---

## 2. Public — Episodes

### GET /api/v1/content/drama/series/{series_id}/episodes 🟡 V2
**Auth**: optional
**Not paginated** (returns full list, typical < 100 episodes)

#### Response 200
```json
{
  "series_id": "<uuid>",
  "episodes": [
    {
      "id": "<uuid>",
      "episode_no": 1,
      "title": "...",
      "duration_seconds": 600,
      "thumbnail_url": "...",
      "is_free": true,
      "unlock_type": "free",
      "pricing": {
        "points_price": "0.0000",
        "credits_price": "0.0000"
      },
      "viewer_context": {
        "is_unlocked": true,
        "can_watch": true,
        "unlocked_via": "free"
      }
    },
    {
      "id": "<uuid>",
      "episode_no": 4,
      "title": "...",
      "duration_seconds": 600,
      "thumbnail_url": "...",
      "is_free": false,
      "unlock_type": "meow_points",
      "pricing": {
        "points_price": "10.0000",
        "credits_price": "1.0000"
      },
      "viewer_context": {
        "is_unlocked": false,
        "can_watch": false,
        "unlocked_via": null
      }
    }
  ]
}
```

`unlock_type` ∈ {`free`, `meow_points`, `meow_credit`, `membership`}.
`unlocked_via` (if unlocked): one of the same values.

---

### GET /api/v1/content/drama/series/{series_id}/episodes/{episode_no} 🟡 V2
**Auth**: optional

#### Response 200
```json
{
  "id": "<uuid>",
  "episode_no": 4,
  "title": "...",
  "description": "...",
  "duration_seconds": 600,
  "thumbnail_url": "...",
  "is_free": false,
  "unlock_type": "meow_points",
  "pricing": {"points_price": "10.0000", "credits_price": "1.0000"},
  "viewer_context": {
    "is_unlocked": true,
    "can_watch": true,
    "unlocked_via": "meow_points",
    "progress_seconds": 0
  },
  "playback": {
    "playback_url": "https://...",
    "hls_url": "https://..."
  },
  "navigation": {
    "previous_episode_no": 3,
    "next_episode_no": 5
  }
}
```

`playback` is **null if not unlocked**. Mobile must check `viewer_context.can_watch`.

---

## 3. Unlock

### POST /api/v1/content/drama/episodes/{episode_id}/unlock 🟡 V2
**Auth**: required
**Idempotency**: yes (required header)

#### Request
```json
{ "payment_method": "meow_points" }
```

`payment_method` ∈ {`meow_points`, `meow_credit`}.

For `unlock_type=membership` episodes, no unlock call needed — handled at episode list time via `DramaAccessService.check_membership_access()`.

#### Response 200
```json
{
  "episode_id": "<uuid>",
  "series_id": "<uuid>",
  "is_unlocked": true,
  "payment_method": "meow_points",
  "points_charged": "10.0000",
  "credits_charged": "0.0000",
  "currency": "MP",
  "ledger_entry_id": "<uuid>",
  "code": null
}
```

If already unlocked: `code: "ALREADY_UNLOCKED"`, no charge.

#### Errors
- 422 `WALLET_INSUFFICIENT_BALANCE` (detail: `{required, available, currency}`)
- 422 `DRAMA_FREE_EPISODE` (cannot unlock free episode)
- 404 `EPISODE_NOT_FOUND`

#### Side effects
- Calls `DramaAccessService.unlock_with_meow_points()` or `unlock_with_meow_credit()`
- Wallet debit via `EconomyService.debit(SPEND, ...)`
- Creates `DramaUnlock` record
- Emits `OutboxEvent`: `content.DramaEpisodeUnlocked`

#### Diff from legacy
- `idempotency_key` header required (legacy lacked)
- Returns `ledger_entry_id` for traceability
- 4 unlock methods documented; membership is implicit

---

## 4. Progress

### GET /api/v1/content/drama/series/{series_id}/progress 🟡 V2
**Auth**: required

#### Response 200
```json
{
  "series_id": "<uuid>",
  "episode_id": "<uuid>",
  "episode_no": 3,
  "progress_seconds": 245,
  "completed": false,
  "updated_at": "..."
}
```

Returns latest progress for the series. 404 if no progress recorded.

### POST /api/v1/content/drama/series/{series_id}/progress 🟡 V2
**Auth**: required
**Idempotency**: yes

#### Request
```json
{
  "episode_id": "<uuid>",
  "progress_seconds": 245,
  "completed": false
}
```

#### Response 200
Updated progress.

#### Side effects
- Upsert `DramaWatchProgress` (per user+series, episode-scope)

---

### POST /api/v1/content/drama/episodes/{episode_id}/progress 🟡 V2
Episode-scoped (finer grain). Same request shape minus `episode_id` (taken from URL).

---

## 5. Favorites

### POST /api/v1/content/drama/series/{series_id}/favorite 🟡 V2
**Auth**: required
**Idempotency**: yes

#### Response 200
```json
{
  "series_id": "<uuid>",
  "is_favorited": true,
  "favorite_count": 679
}
```

### DELETE /api/v1/content/drama/series/{series_id}/favorite 🟡 V2
Symmetric.

#### Side effects
- Creates/deletes `DramaFavorite`
- Adjusts favorite_count
- Emits `OutboxEvent`: `content.DramaFavorited` / `content.DramaUnfavorited`

---

## 6. Comments

Mirrors video comments structure.

### GET /api/v1/content/drama/series/{series_id}/comments 🟡 V2
Cursor-paginated.

### POST /api/v1/content/drama/series/{series_id}/comments 🟡 V2
**Auth**: required

#### Request
```json
{ "content": "...", "parent_id": null }
```

---

## 7. Creator management

❌ Mobile-unused per legacy analysis. Implementing for creator dashboard (web/desktop).

### GET / POST /api/v1/content/drama/me 🔵 V3
List + create drama series (creator + admin only).

### GET / PATCH / DELETE /api/v1/content/drama/me/{series_id} 🔵 V3
Manage series. DELETE is soft.

### GET / POST /api/v1/content/drama/me/{series_id}/episodes 🔵 V3
List + create episodes (multipart for file).

### GET / PATCH / DELETE /api/v1/content/drama/me/{series_id}/episodes/{episode_id} 🔵 V3
Manage episodes. DELETE is soft, recounts `total_episodes`.

---

## 8. Outbox events emitted

| Event | When |
|---|---|
| `content.DramaSeriesCreated` | Creator creates series |
| `content.DramaSeriesUpdated` | Metadata change |
| `content.DramaSeriesDeleted` | Soft delete |
| `content.DramaEpisodeCreated` | Episode upload |
| `content.DramaEpisodeUpdated` | Metadata change |
| `content.DramaEpisodeDeleted` | Soft delete |
| `content.DramaEpisodeUnlocked` | After unlock |
| `content.DramaSeriesFavorited` | Favorite |
| `content.DramaSeriesUnfavorited` | Unfavorite |
| `content.DramaSeriesCommented` | Comment posted |
| `content.DramaSeriesShared` | Share recorded |
| `content.DramaSeriesViewed` | View tracked |
| `content.DramaGifted` | Gift (see gift.md) |
| `content.DramaProgressUpdated` | Watch progress upsert |

---

## 9. Drama unlock methods (full table)

| Method | Episode flag | Endpoint | Wallet |
|---|---|---|---|
| Free | `is_free=true` | none (immediate `can_watch=true`) | none |
| MeowPoints | `unlock_type=meow_points` | POST `/episodes/{id}/unlock` with `payment_method=meow_points` | PointWallet debit |
| MeowCredit | `unlock_type=meow_credit` | POST `/episodes/{id}/unlock` with `payment_method=meow_credit` | CreditWallet debit |
| Membership | `unlock_type=membership` | implicit — checked at episode list / detail | none |

**Membership access check**:
```python
DramaAccessService.has_active_membership(user_id, series_id) -> bool
```
Returns true if user has any active membership of plan tier eligible for this drama. Series-to-plan eligibility configured per series (`membership_plans_required` field on DramaSeries — many-to-many).

---

## 10. V1 vs V2 scope

| Feature | V1 | V2 | V3 |
|---|---|---|---|
| Public series + episodes catalog | | 🟡 | |
| Episode unlock (4 methods) | | 🟡 | |
| Watch progress | | 🟡 | |
| Favorites | | 🟡 | |
| Comments | | 🟡 | |
| Drama gift (see gift.md) | | 🟡 | |
| Creator drama management | | | 🔵 |
| Series-level recommendations | | | 🔵 |
