# Content — Live Contract (Django side)

Covers: live stream metadata, viewer-facing REST endpoints, chat REST + WebSocket, creator/broadcaster controls.

**App**: `apps/content/live/` (Django metadata + chat orchestration)
**Companion**: `services/live_runtime/` (gRPC realtime service — see live-runtime.md)
**Legacy reference**: `MOBILE_API_CONTRACT_FULL.md` §19-24
**Priority**: 🔵 V3 (mobile-critical but last in 16-week plan)

---

## Architecture split

```
mobile/web
   │
   │ REST (metadata, chat history, gift send)
   ▼
apps/content/live/   ←──┐
   │                    │
   │ via service-layer  │
   ▼                    │
EconomyService           │
   │                    │
   └──→ OutboxEvent ────┘
                         │
                         ▼
              services/live_runtime/ (gRPC)
              - WebSocket gateway
              - Ant Media bridge
              - Gift broadcast
              - Viewer presence
```

**Django owns**: stream metadata, status, chat history persistence, gift wallet logic, payment.

**Live Runtime owns**: realtime WebSocket connections, Ant Media interaction, viewer presence, gift event broadcast.

---

## 1. Viewer — Browse

### GET /api/v1/content/live/streams 🔵 V3
**Auth**: optional
**Cursor-paginated**

#### Request (query)
```
?cursor=<>&limit=20
&status=live|ready|idle|ended
&owner_id=<uuid>
```

#### Response 200
```json
{
  "results": [
    {
      "id": "<uuid>",
      "title": "...",
      "description": "...",
      "owner": {"id": "<uuid>", "display_name": "...", "avatar_url": "..."},
      "category": {"id": "<uuid>", "name": "...", "slug": "..."},
      "visibility": "public",
      "thumbnail_url": "...",
      "preview_image_url": "...",
      "snapshot_url": "...",
      "status": "live",
      "effective_status": "live",
      "viewer_count": 42,
      "created_at": "...",
      "started_at": "..."
    }
  ],
  "cursor": {"next": "...", "prev": null}
}
```

#### Diff from legacy
- Cursor pagination (legacy had `pagination_class=None`)
- `owner` nested
- Removed: `channel_id`, `channel_name`, `creator_live_urls`, all `_url` fields except documented thumbnail/preview/snapshot

---

### GET /api/v1/content/live/streams/{stream_id} 🔵 V3
Single stream. If viewer is owner, response includes `broadcaster_config`:

```json
{
  ...,
  "broadcaster_config": {
    "stream_key": "...",
    "rtmp_url": "rtmp://...",
    "webrtc_publish_config": { ... }
  }
}
```

---

### GET /api/v1/content/live/streams/{stream_id}/status 🔵 V3
Lightweight polling endpoint.

#### Response 200
```json
{
  "id": "<uuid>",
  "status": "live",
  "effective_status": "live",
  "can_start": false,
  "can_end": true,
  "viewer_count": 42,
  "publish": {"connected": true, "status": "..."},
  "play": {"connected": true, "status": "..."}
}
```

---

### GET /api/v1/content/live/streams/{stream_id}/watch-config 🔵 V3
**Auth**: optional
**Critical for mobile playback**

#### Response 200
```json
{
  "live_id": "<uuid>",
  "status": "live",
  "effective_status": "live",
  "viewer_count": 42,
  "playback": {
    "mode": "webrtc",
    "stream_id": "<ant-media-stream-id>",
    "websocket_url": "wss://ant-media-server/websocket",
    "hls_url": "https://cdn/stream.m3u8",
    "connected": true
  },
  "fallback": {"mode": "hls", "hls_url": "https://..."},
  "thumbnail_url": "...",
  "preview_image_url": "...",
  "snapshot_url": "..."
}
```

#### Side effects
- Increments viewer_count once per unique user/IP per 60s (Redis dedup)
- Calls `LiveRuntimeService.GetWatchConfig(stream_id)` via gRPC

#### Diff from legacy
- Calls Live Runtime gRPC instead of direct AntMediaLiveAdapter
- Same response shape (mobile compatibility)

---

### GET /api/v1/content/live/streams/{stream_id}/products 🔵 V3
List products bound to this stream (for promotion).

### GET /api/v1/content/live/streams/{stream_id}/gifts 🔵 V3
Static gift catalog scoped to this stream. See gift.md.

---

## 2. Viewer — Chat REST

### GET /api/v1/content/live/streams/{stream_id}/chat/messages 🔵 V3
**Auth**: optional (visibility-restricted)
**Cursor-paginated** (cursor-style via `after_id`)

#### Request (query)
```
?after_id=<uuid>
&limit=50    (max 100)
```

#### Response 200
```json
{
  "results": [
    {
      "id": "<uuid>",
      "live_id": "<uuid>",
      "type": "text",
      "content": "...",
      "user": {"id": "<uuid>", "display_name": "...", "avatar_url": "..."},
      "product": null,
      "is_pinned": false,
      "created_at": "..."
    }
  ],
  "next_after_id": "<uuid>"
}
```

`type` ∈ {`text`, `product`, `gift`}.

For `type=gift`, `payload` carries gift context:
```json
{
  "type": "gift",
  "payload": {
    "sender_id": "<uuid>",
    "sender_name": "...",
    "amount": "100.0000",
    "currency": "MP",
    "payment_method": "meow_points"
  }
}
```

---

### POST /api/v1/content/live/streams/{stream_id}/chat/messages 🔵 V3
**Auth**: required
**Idempotency**: yes

#### Request
```json
{
  "content": "...",
  "product_id": null
}
```

#### Response 201
Created message.

#### Errors
- 422 `LIVE_STREAM_NOT_LIVE` (cannot post to ended stream)

#### Side effects
- Creates `LiveChatMessage`
- Emits `OutboxEvent`: `content.live.ChatMessagePosted`
- **gRPC call to `LiveRuntimeService.BroadcastChat(stream_id, message)`** — broadcasts to all WebSocket viewers

---

### DELETE /api/v1/content/live/streams/{stream_id}/chat/messages/{message_id} 🔵 V3
**Auth**: required (broadcaster or message author)

Soft delete. Triggers broadcast deletion event via Live Runtime.

### PUT /api/v1/content/live/streams/{stream_id}/chat/messages/{message_id}/pin 🔵 V3
**Auth**: required (broadcaster only)

---

## 3. Viewer — Chat WebSocket

Direct WebSocket is **handled by Live Runtime gRPC service**, not Django. See live-runtime.md.

Mobile connects to:
```
wss://<live-runtime>/ws/v1/live/<live_id>/chat
```

with `Authorization: Bearer <jwt>` in query string or first frame.

Django no longer hosts the WebSocket. Chat REST endpoints remain for history and as fallback for sending.

#### Diff from legacy
**Architectural change**. Legacy hosted WebSocket via Django Channels. New platform offloads realtime to Live Runtime gRPC service.

---

## 4. Viewer — Send Gift

See gift.md for full contract. Endpoint summary:

### POST /api/v1/content/live/streams/{stream_id}/gifts/send 🔵 V3
**Auth**: required
**Idempotency**: yes (required)

**Critical**: this is the only gift endpoint that triggers a Live Runtime broadcast (video/drama gifts are silent).

#### Side effects
- Wallet debit (sync, Django)
- Wallet credit to receiver (sync, Django)
- GiftTransaction (sync, Django)
- Emits `OutboxEvent`: `content.live.GiftSent`
- **Dispatcher → gRPC call to `LiveRuntimeService.BroadcastGift(stream_id, gift_event)`**
- All viewers connected to WebSocket receive `gift_event` payload

---

## 5. Broadcaster — Stream lifecycle

### POST /api/v1/content/live/me/streams 🔵 V3
**Auth**: required + creator
**Idempotency**: yes
**Content-Type**: `multipart/form-data`

#### Request
```
title: string
description: string
category_id: UUID (optional)
visibility: "public" | "private"
thumbnail: <file> (optional)
```

#### Response 201
Stream object with `broadcaster_config` (stream_key, rtmp_url, webrtc_publish_config).

#### Side effects
- Creates `LiveStream` (status = `idle`)
- Calls `LiveRuntimeService.CreateStream(stream_id, owner_id)` → returns Ant Media credentials
- Emits `OutboxEvent`: `content.live.StreamCreated`

---

### POST /api/v1/content/live/me/streams/{stream_id}/prepare 🔵 V3
Owner-only. Prepares Ant Media session for incoming publish.

### POST /api/v1/content/live/me/streams/{stream_id}/start 🔵 V3
Owner-only. Transitions to `live`.

#### Request
```json
{ "publish_session_id": "..." }
```

#### Response 200
```json
{
  "ok": true,
  "status": "live",
  "already_started": false,
  "stream": { /* full stream object */ }
}
```

#### Side effects
- Updates status to `live`
- Calls `LiveRuntimeService.StartBroadcast(stream_id)`
- Emits `OutboxEvent`: `content.live.StreamStarted`

#### Errors
- 409 `LIVE_INVALID_STATE` (can only start from idle/ready)
- 502 `LIVE_RUNTIME_UNAVAILABLE`

---

### POST /api/v1/content/live/me/streams/{stream_id}/end 🔵 V3
Owner-only. Terminal state.

#### Side effects
- Updates status to `ended`
- Calls `LiveRuntimeService.StopBroadcast(stream_id)`
- Emits `OutboxEvent`: `content.live.StreamEnded`

---

### GET /api/v1/content/live/me/streams 🔵 V3
Cursor-paginated owner's streams (all statuses).

### PATCH /api/v1/content/live/me/streams/{stream_id} 🔵 V3
Update metadata.

### GET /api/v1/content/live/me/streams/{stream_id}/quick-start 🔵 V3
Convenience: reuse existing idle/ready stream OR create new. `?fresh=true` cleans up zombies via gRPC.

---

## 6. Broadcaster — Products & Payment methods

⚠️ Per legacy analysis: mobile-unused. Implementing for creator dashboard.

### CRUD /api/v1/content/live/me/streams/{stream_id}/products 🔵 V3
Bind products to stream.

### CRUD /api/v1/content/live/me/streams/{stream_id}/payment-methods 🔵 V3
Configure per-stream payment methods.

---

## 7. State machine

```
IDLE ─(prepare)─→ READY ─(start)─→ LIVE ─(end)─→ ENDED (terminal)
                                             ↓
                                           FAILED (terminal)
```

State transitions invoked via:
- `prepare` endpoint → IDLE → READY
- `start` endpoint → READY → LIVE
- `end` endpoint → LIVE → ENDED
- Live Runtime callback → any → FAILED (Ant Media disconnect)

---

## 8. Outbox events emitted

| Event | When | Subscribers |
|---|---|---|
| `content.live.StreamCreated` | After create | analytics |
| `content.live.StreamStarted` | After start | notification (followers), analytics |
| `content.live.StreamEnded` | After end | analytics |
| `content.live.StreamFailed` | Ant Media failure | alerting |
| `content.live.GiftSent` | After gift send | **Live Runtime** (broadcasts), analytics, notification |
| `content.live.ChatMessagePosted` | After message create | **Live Runtime** (broadcasts) |
| `content.live.ChatMessageDeleted` | After delete | Live Runtime |
| `content.live.ChatMessagePinned` | After pin | Live Runtime |
| `content.live.ViewerJoined` | (emitted by Live Runtime back to Django) | analytics |
| `content.live.ViewerLeft` | (from Live Runtime) | analytics |

---

## 9. Open contract items (deferred to V3 design phase)

- Stream tipping / sponsorship beyond gift system
- Recording / VOD generation
- Co-streaming / guest invites
- Scheduled streams (vs ad-hoc)
- Moderation tools (ban viewer, mute)
- Stream-level analytics dashboard

These are in scope for V3 but contract details TBD.

---

## 10. V1 vs V3 scope

V1 ships **none** of this. V2 may ship metadata-only browse if needed for mobile cutover. V3 is full Live + Live Runtime.

Until V3, mobile continues to use legacy backend for Live features.
