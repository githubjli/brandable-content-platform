# Library / Activity Contract

Covers: user activity tabs (history, liked, purchased, gifts sent, gifts received).

**App**: aggregator endpoints in `apps/identity/views/library.py` (data sourced from multiple apps via service calls)
**Legacy reference**: `MOBILE_API_CONTRACT_FULL.md` §5

---

## Naming note

Mobile UI labels this **"Activity"**, but URL path stays `/library` for consistency. Future rename TBD.

---

## 1. History

### GET /api/v1/account/library/history 🟢 V1
**Auth**: required
**Cursor-paginated**

#### Response 200
```json
{
  "results": [
    {
      "type": "drama",
      "id": "<series_uuid>",
      "title": "...",
      "cover_url": "...",
      "progress_seconds": 245,
      "duration_seconds": 600,
      "series_id": "<uuid>",
      "episode_id": "<uuid>",
      "episode_no": 3,
      "updated_at": "..."
    },
    {
      "type": "video",
      "id": "<video_uuid>",
      "title": "...",
      "thumbnail_url": "...",
      "progress_seconds": 0,
      "duration_seconds": 0,
      "updated_at": "..."
    }
  ],
  "cursor": {"next": "...", "prev": null}
}
```

Type discriminator: `drama` | `video`.

#### Diff from legacy
- Cursor pagination (was page-based)
- UUID ids (was integer)
- `progress_seconds` and `duration_seconds` always present (0 for video items)

---

## 2. Liked

### GET /api/v1/account/library/liked 🟢 V1
**Auth**: required
**Cursor-paginated**

#### Response 200
```json
{
  "results": [
    {
      "type": "video",
      "id": "<uuid>",
      "title": "...",
      "thumbnail_url": "...",
      "owner": {
        "id": "<uuid>",
        "display_name": "...",
        "avatar_url": "..."
      },
      "liked_at": "..."
    }
  ],
  "cursor": {"next": "...", "prev": null}
}
```

Currently only `type: video`. Future drama likes will use same shape with `type: drama`.

---

## 3. Purchased

Mixed feed: drama episode unlocks, payment orders, memberships.

### GET /api/v1/account/library/purchased 🟢 V1
**Auth**: required
**Cursor-paginated**

#### Response 200
```json
{
  "results": [
    {
      "type": "drama_episode",
      "id": "<unlock_uuid>",
      "series_id": "<uuid>",
      "series_title": "...",
      "episode_id": "<uuid>",
      "episode_no": 3,
      "cover_url": "...",
      "payment_method": "meow_points",
      "points_charged": "10.0000",
      "credits_charged": "0.0000",
      "purchased_at": "..."
    },
    {
      "type": "order",
      "id": "<order_uuid>",
      "order_no": "ORD-...",
      "business_kind": "PRODUCT",
      "amount": {"amount": "29.99", "currency": "USD"},
      "status": "paid",
      "title": "Product name",
      "thumbnail_url": "...",
      "purchased_at": "..."
    },
    {
      "type": "membership",
      "id": "<membership_uuid>",
      "plan_code": "PRO_MONTHLY",
      "plan_name": "Pro Monthly",
      "starts_at": "...",
      "ends_at": "...",
      "status": "active",
      "purchased_at": "..."
    }
  ],
  "cursor": {"next": "...", "prev": null}
}
```

Type discriminator: `drama_episode` | `order` | `membership`.

#### Diff from legacy
- Cursor pagination
- Money fields nested under `amount` object with currency
- UUIDs everywhere

---

## 4. Gifts Sent

### GET /api/v1/account/library/gifts/sent 🟢 V1
**Auth**: required
**Cursor-paginated**

#### Response 200
```json
{
  "results": [
    {
      "id": "<gift_transaction_uuid>",
      "direction": "sent",
      "gift_name": null,
      "amount": "100.0000",
      "currency": "MP",
      "points_amount": "100.0000",
      "credits_amount": "0.0000",
      "payment_method": "meow_points",
      "receiver": {
        "id": "<uuid>",
        "display_name": "...",
        "avatar_url": "..."
      },
      "content": {
        "type": "live_stream",
        "id": "<uuid>",
        "title": "..."
      },
      "created_at": "..."
    }
  ],
  "cursor": {"next": "...", "prev": null}
}
```

`content.type` ∈ {`video`, `drama_series`, `live_stream`}.

`gift_name` is non-null only for fixed-gift mode (legacy; new platform deprecates fixed gifts — see deprecated.md).

#### Diff from legacy
- `amount` is the total transferred (string Decimal)
- `currency` always present (was missing)
- `payment_method` made explicit (was inferred from points_amount/credits_amount)

---

## 5. Gifts Received

### GET /api/v1/account/library/gifts/received 🟢 V1
**Auth**: required
**Cursor-paginated**

#### Response 200
Same shape as sent, but `sender` instead of `receiver` and `direction: received`.

---

## 6. Internal architecture

Library endpoints don't own data; they aggregate from:

| Tab | Source domain | Service call |
|---|---|---|
| history | `content.video`, `content.drama` | `VideoViewService.list_for_user(...)`, `DramaProgressService.list_for_user(...)` |
| liked | `content.video` | `VideoLikeService.list_for_user(...)` |
| purchased | `content.drama` (unlocks), `payments` (orders), `membership` (active/past) | merge with sort key `created_at desc` |
| gifts sent | `gift` (cross-content) | `GiftService.list_sent(...)` |
| gifts received | `gift` | `GiftService.list_received(...)` |

Each tab is paginated **independently** at the service layer with cursor merging if data spans multiple sources (purchased only).

---

## 7. Performance considerations

- Each tab caps result size to `limit` (max 100).
- Cursor encodes domain partition + offset for mixed-feed tabs.
- Library queries hit denormalized counters; no aggregation at request time.
- Service-layer caching: 30s TTL on counts; full results not cached.

---

## 8. Not implemented (per mobile analysis)

- **Downloads tab**: mobile UI has no download functionality. Not implemented.
- **Favorites (drama-only)**: covered separately via `GET /api/v1/content/drama/series/{id}/favorite` toggle in content-drama.md, not as a Library tab.
