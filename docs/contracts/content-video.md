# Content — Video Contract

Covers: public video catalog, interactions (like / comment / share / view tracking), creator video management. **Does NOT cover gifts** (see gift.md).

**App**: `apps/content/video/`
**Legacy reference**: `MOBILE_API_CONTRACT_FULL.md` §15-16
**Priority**: 🟡 V2 (mobile uses, but not in V1 backend scope per 16-week plan)

> Per V1 mobile-only cutover discussion: this domain is needed for mobile cutover. If V1 scope is constrained, mobile keeps using legacy backend for video until V2.

---

## 1. Public catalog

### GET /api/v1/content/video/public 🟡 V2
**Auth**: optional (richer response if authed)
**Cursor-paginated**

#### Request (query)
```
?cursor=<>&limit=20
&category=<slug>
&access_type=public|members|...
&search=<text>
&ordering=-created_at|-view_count|-like_count
```

#### Response 200
```json
{
  "results": [
    {
      "id": "<uuid>",
      "title": "...",
      "description": "...",
      "owner": {
        "id": "<uuid>",
        "display_name": "...",
        "avatar_url": "...",
        "is_creator": true
      },
      "category": {"id": "<uuid>", "name": "...", "slug": "..."},
      "visibility": "public",
      "file_url": "https://...",
      "thumbnail_url": "https://...",
      "duration_seconds": 245,
      "counts": {
        "view": 12345,
        "like": 678,
        "comment": 42,
        "share": 12,
        "gift_amount": "100.0000",
        "gift_currency": "MP"
      },
      "viewer_context": {
        "is_liked": false,
        "can_watch": true,
        "is_following_owner": false
      },
      "created_at": "..."
    }
  ],
  "cursor": {"next": "...", "prev": null}
}
```

#### Diff from legacy
- Owner info nested under `owner`
- Counts nested under `counts`
- Viewer-specific state nested under `viewer_context`
- Removed legacy aliases: `channel_id`, `channel_name`, `subscriber_count`, `is_subscribed`
- Cursor pagination

---

### GET /api/v1/content/video/public/{video_id} 🟡 V2
**Auth**: optional

#### Response 200
Single video, same shape as list item + `description_html` (rendered).

---

### GET /api/v1/content/video/public/{video_id}/interactions 🟡 V2
**Auth**: optional

Replaces legacy `/interaction-summary/`.

#### Response 200
```json
{
  "video_id": "<uuid>",
  "counts": {
    "view": 12345,
    "like": 678,
    "comment": 42,
    "share": 12,
    "gift_amount": "100.0000",
    "gift_currency": "MP"
  },
  "viewer_context": {
    "is_liked": false,
    "is_following_owner": false
  },
  "owner_follower_count": 1234
}
```

---

### GET /api/v1/content/video/public/{video_id}/related 🟡 V2
Cursor-paginated related videos.

### GET /api/v1/content/video/public/{video_id}/recommendations 🟡 V2
Cursor-paginated recommendations.

---

## 2. Interactions

### POST /api/v1/content/video/public/{video_id}/like 🟡 V2
**Auth**: required
**Idempotency**: yes (idempotent — no-op if already liked)

#### Response 200
```json
{
  "video_id": "<uuid>",
  "is_liked": true,
  "like_count": 679
}
```

#### Side effects
- Creates `VideoLike` (idempotent via UNIQUE(user_id, video_id))
- Increments `video.like_count` atomically (`F() + 1` + `SELECT FOR UPDATE` on video row for now; eventually denormalized counter via OutboxEvent)
- Emits `OutboxEvent`: `content.VideoLiked`

### DELETE /api/v1/content/video/public/{video_id}/like 🟡 V2
Symmetric.

---

### GET /api/v1/content/video/public/{video_id}/comments 🟡 V2
**Auth**: optional
**Cursor-paginated**

#### Response 200
```json
{
  "results": [
    {
      "id": "<uuid>",
      "content": "...",
      "user": {"id": "<uuid>", "display_name": "...", "avatar_url": "..."},
      "parent_id": null,
      "reply_count": 3,
      "created_at": "..."
    }
  ],
  "cursor": {"next": "...", "prev": null}
}
```

### POST /api/v1/content/video/public/{video_id}/comments 🟡 V2
**Auth**: required
**Idempotency**: yes

#### Request
```json
{
  "content": "...",
  "parent_id": null
}
```

#### Response 201
Comment object.

#### Errors
- 422 `COMMENT_PARENT_INVALID` (parent_id doesn't belong to this video)
- 422 `COMMENT_TOO_LONG` (max 2000 chars)

#### Side effects
- Creates `VideoComment`
- Increments `video.comment_count`
- If reply: increments `parent.reply_count`
- Emits `OutboxEvent`: `content.VideoCommented`

---

### POST /api/v1/content/video/public/{video_id}/share 🟡 V2
**Auth**: optional (anonymous shares allowed)
**Idempotency**: no (each call tracks separately for analytics)

#### Request
```json
{ "channel": "whatsapp" }
```

`channel` optional, max 64 chars.

#### Response 200
```json
{
  "video_id": "<uuid>",
  "share_count": 13
}
```

#### Side effects
- Creates `VideoShare` record (logs IP, user_agent)
- Increments share_count

---

### POST /api/v1/content/video/public/{video_id}/view 🟡 V2
**Auth**: optional
**Idempotency**: server-enforced (1 view per user/IP per minute)

#### Response 200
```json
{
  "video_id": "<uuid>",
  "view_count": 12346
}
```

#### Side effects
- Creates `VideoView` if not deduplicated
- Increments `video.view_count`

---

## 3. Creator video management

### GET /api/v1/content/video/me 🟡 V2
**Auth**: required
**Cursor-paginated**

List of creator's own videos (all visibilities).

### POST /api/v1/content/video/me 🟡 V2
**Auth**: required (must be creator)
**Idempotency**: yes
**Content-Type**: `multipart/form-data`

#### Request
```
title: string
description: string
file: <video file>
thumbnail: <image file> (optional)
category_id: UUID
visibility: "public" | "private" | "unlisted"
access_type: "free" | "members_only" | "...
preview_seconds: integer (default 0)
```

#### Response 201
Video object.

#### Side effects
- Creates Video record
- Auto-generates thumbnail at 1.0s if not provided
- Emits `OutboxEvent`: `content.VideoCreated`
- **Transcoding job NOT in V1 scope** (per legacy). V3 will integrate transcoding via Celery + ffmpeg or cloud service.

---

### GET /api/v1/content/video/me/{video_id} 🟡 V2
Owner only.

### PATCH /api/v1/content/video/me/{video_id} 🟡 V2
Update metadata (no file changes here; file upload only via initial POST).

### DELETE /api/v1/content/video/me/{video_id} 🟡 V2
**Soft delete**: sets `is_active=false`. New platform avoids hard delete; cascades not allowed in V1.

### POST /api/v1/content/video/me/{video_id}/regenerate-thumbnail 🟡 V2
**Auth**: required (owner)

#### Request
```json
{ "time_offset_seconds": 5.0 }
```

---

## 4. Outbox events emitted

| Event | When |
|---|---|
| `content.VideoCreated` | After upload |
| `content.VideoUpdated` | After metadata change |
| `content.VideoDeleted` | After soft delete |
| `content.VideoLiked` | After like |
| `content.VideoUnliked` | After unlike |
| `content.VideoCommented` | After comment posted |
| `content.VideoShared` | After share recorded |
| `content.VideoViewed` | After view tracked (sampled) |
| `content.VideoGifted` | After gift sent (see gift.md) |

---

## 5. V1 vs V2 scope

| Feature | V1 | V2 | V3 |
|---|---|---|---|
| Public catalog | | 🟡 | |
| Detail + interaction-summary | | 🟡 | |
| Comments (CRUD + threading) | | 🟡 | |
| Like / share / view tracking | | 🟡 | |
| Creator upload (without transcoding) | | 🟡 | |
| Recommendations | | | 🔵 |
| Real transcoding | | | 🔵 |
| CDN integration | | | 🔵 |
| Video gift (see gift.md) | | 🟡 | |
