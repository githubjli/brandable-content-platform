# LiveRuntimeService gRPC Contract

**Third gRPC service** (V3 at W15-16). Handles Ant Media integration, viewer WebSocket connections, gift broadcasts, viewer presence.

**Service location**: `services/live_runtime/`
**Implementation**: Python (grpcio + asyncio) in V1; Go rewrite likely under load
**Proto package**: `live.v1`

---

## Responsibility split with Django

| Concern | Owner | Reason |
|---|---|---|
| Live stream metadata (title, description, owner, status) | Django (`apps/content/live/`) | Persistent; needs admin tooling |
| Chat message persistence | Django | Append-only, needs Library access |
| Gift transaction (wallet debit/credit, ledger) | Django | Money is sacred — atomic with DB |
| Ant Media REST/WebSocket integration | **Live Runtime** | Different protocol; sync expensive in Django |
| Viewer presence (in-memory) | **Live Runtime** | Ephemeral, high churn |
| Viewer WebSocket sessions | **Live Runtime** | Long-lived connections, async I/O native |
| Gift broadcast to viewers | **Live Runtime** | Triggered by Django Outbox |
| Chat broadcast to viewers | **Live Runtime** | Same |
| Viewer count aggregation | **Live Runtime** | Real-time |
| RTMP/WebRTC publish config issuance | **Live Runtime** | Ant Media coupling |

---

## 1. Proto definition

`proto/live/v1/live_runtime.proto`:

```proto
syntax = "proto3";

package live.v1;

import "common/v1/common.proto";
import "google/protobuf/timestamp.proto";

service LiveRuntimeService {
  rpc Ping(PingRequest) returns (PingResponse);

  // --- Stream lifecycle ---
  rpc CreateStream(CreateStreamRequest) returns (StreamConfig) {
    option (idempotency.level) = IDEMPOTENT;
  }
  rpc StartBroadcast(StartBroadcastRequest) returns (StartBroadcastResponse);
  rpc StopBroadcast(StopBroadcastRequest) returns (StopBroadcastResponse);
  rpc DeleteStream(DeleteStreamRequest) returns (DeleteStreamResponse);

  // --- Viewer playback config ---
  rpc GetWatchConfig(GetWatchConfigRequest) returns (WatchConfig);
  rpc GetStreamStatus(GetStreamStatusRequest) returns (StreamStatusResponse);

  // --- Broadcasts (from Django Outbox handlers) ---
  rpc BroadcastChat(BroadcastChatRequest) returns (BroadcastResponse);
  rpc BroadcastGift(BroadcastGiftRequest) returns (BroadcastResponse);
  rpc BroadcastModeration(BroadcastModerationRequest) returns (BroadcastResponse);

  // --- Viewer events back to Django ---
  rpc StreamViewerEvents(StreamViewerEventsRequest) returns (stream ViewerEvent);
}

// --- Stream lifecycle ---

message CreateStreamRequest {
  string idempotency_key = 1;
  string stream_id = 2;        // UUID from Django
  string owner_user_id = 3;
  string title = 4;
  string visibility = 5;        // 'public' | 'private'
  string trace_id = 6;
}

message StreamConfig {
  string stream_id = 1;
  string stream_key = 2;       // for publisher
  string rtmp_url = 3;
  WebRTCPublishConfig webrtc = 4;
  google.protobuf.Timestamp created_at = 5;
}

message WebRTCPublishConfig {
  string websocket_url = 1;
  string ice_servers_json = 2;
}

message StartBroadcastRequest {
  string idempotency_key = 1;
  string stream_id = 2;
  string publish_session_id = 3;
}

message StartBroadcastResponse {
  string stream_id = 1;
  string status = 2;       // 'live' | 'failed'
  bool already_started = 3;
  string ant_media_session_id = 4;
}

message StopBroadcastRequest {
  string idempotency_key = 1;
  string stream_id = 2;
}

message StopBroadcastResponse {
  string stream_id = 1;
  string status = 2;       // 'ended'
}

message DeleteStreamRequest {
  string stream_id = 1;
}

message DeleteStreamResponse {
  string stream_id = 1;
  bool deleted = 2;
}

// --- Watch config (for viewers) ---

message GetWatchConfigRequest {
  string stream_id = 1;
  string viewer_user_id = 2;       // optional; for dedup viewer counting
  string viewer_ip = 3;             // for anonymous dedup
}

message WatchConfig {
  string live_id = 1;
  string status = 2;           // raw: 'idle' | 'ready' | 'live' | 'ended' | 'failed'
  string effective_status = 3; // normalized from Ant Media
  int32 viewer_count = 4;
  Playback playback = 5;
  Playback fallback = 6;
  string thumbnail_url = 7;
  string preview_image_url = 8;
  string snapshot_url = 9;
}

message Playback {
  string mode = 1;           // 'webrtc' | 'hls'
  string stream_id = 2;       // Ant Media internal id
  string websocket_url = 3;
  string hls_url = 4;
  bool connected = 5;
}

message GetStreamStatusRequest {
  string stream_id = 1;
}

message StreamStatusResponse {
  string stream_id = 1;
  string status = 2;
  string effective_status = 3;
  bool can_start = 4;
  bool can_end = 5;
  int32 viewer_count = 6;
  PublishHealth publish = 7;
  PlayHealth play = 8;
}

message PublishHealth {
  bool connected = 1;
  string status = 2;
}

message PlayHealth {
  bool connected = 1;
  string status = 2;
}

// --- Broadcasts (Django → Runtime → viewers) ---

message BroadcastChatRequest {
  string idempotency_key = 1;
  string stream_id = 2;
  ChatMessagePayload message = 3;
}

message ChatMessagePayload {
  string message_id = 1;
  string sender_user_id = 2;
  string sender_display_name = 3;
  string sender_avatar_url = 4;
  string type = 5;          // 'text' | 'product' | 'gift'
  string content = 6;
  bytes payload_json = 7;
  google.protobuf.Timestamp created_at = 8;
}

message BroadcastGiftRequest {
  string idempotency_key = 1;
  string stream_id = 2;
  GiftPayload gift = 3;
}

message GiftPayload {
  string gift_transaction_id = 1;
  string sender_user_id = 2;
  string sender_display_name = 3;
  string sender_avatar_url = 4;
  string amount = 5;            // decimal string
  string currency = 6;
  string payment_method = 7;
  string gift_code = 8;
  google.protobuf.Timestamp sent_at = 9;
}

message BroadcastModerationRequest {
  string idempotency_key = 1;
  string stream_id = 2;
  string action = 3;            // 'delete_message' | 'pin_message'
  string message_id = 4;
}

message BroadcastResponse {
  bool delivered = 1;
  int32 recipient_count = 2;
}

// --- Viewer events (Runtime → Django) ---

message StreamViewerEventsRequest {
  string subscriber_id = 1;        // Django consumer id for resume
}

message ViewerEvent {
  oneof event {
    ViewerJoined viewer_joined = 1;
    ViewerLeft viewer_left = 2;
    StreamFailedEvent stream_failed = 3;
  }
  google.protobuf.Timestamp event_time = 4;
}

message ViewerJoined {
  string stream_id = 1;
  string viewer_user_id = 2;
}

message ViewerLeft {
  string stream_id = 1;
  string viewer_user_id = 2;
  int32 watch_duration_seconds = 3;
}

message StreamFailedEvent {
  string stream_id = 1;
  string reason = 2;
}

// --- Health ---

message PingRequest {}
message PingResponse {
  string version = 1;
  google.protobuf.Timestamp server_time = 2;
}
```

---

## 2. Viewer WebSocket gateway

Mobile viewers connect to:
```
wss://live.example.com/ws/v1/live/<live_id>/chat
```

with `Authorization: Bearer <jwt>` in query string.

### Inbound from mobile
```json
{ "action": "post_message", "data": { "idempotency_key": "...", "content": "...", "product_id": "..." } }
```

### Outbound to mobile
```json
{
  "type": "chat_message",
  "data": { ...ChatMessagePayload... }
}
{
  "type": "gift_event",
  "data": { ...GiftPayload... }
}
{
  "type": "viewer_count_update",
  "data": { "count": 42 }
}
{
  "type": "stream_status_change",
  "data": { "status": "ended" }
}
{
  "type": "error",
  "data": { "code": "STREAM_NOT_LIVE", "detail": "..." }
}
```

Inbound `post_message`:
- Live Runtime validates content
- Calls Django REST API `POST /api/v1/content/live/streams/{stream_id}/chat/messages` (with service account JWT)
- Django persists and emits Outbox event
- Dispatcher calls `BroadcastChat` back to Runtime
- Runtime broadcasts to all subscribers

This indirection ensures persistence (Django) and broadcast (Runtime) are decoupled.

---

## 3. Authentication

- WebSocket: end-user JWT (validated by Runtime via JWKS)
- gRPC: service account JWT for inbound calls from Django; service-to-service for outbound to Identity if needed

---

## 4. Architecture

```
mobile (WS) ──→ Live Runtime ──→ Ant Media REST/WS
                  │  ▲
                  │  │
   BroadcastChat  │  │ (via OutboxEvent dispatcher → gRPC)
   BroadcastGift  │  │
                  │  │
                  ▼  │
                Django (REST API for persistence + Outbox emit)
```

---

## 5. RPC semantics

### CreateStream
- Called by Django when broadcaster creates a live stream
- Returns Ant Media stream key + RTMP URL + WebRTC config
- Idempotent

### StartBroadcast / StopBroadcast
- Called by Django on `start` / `end` endpoint
- Live Runtime calls Ant Media REST to begin/end the broadcast
- Returns status; Django updates its DB based on success

### GetWatchConfig
- Called by Django on `/watch-config/` endpoint
- Increments viewer count (dedup per user/IP per 60s in Redis)
- Returns playback URLs

### BroadcastChat / BroadcastGift / BroadcastModeration
- Called by Outbox dispatcher after Django persists the message/gift
- Idempotent (replay-safe via idempotency_key)
- Live Runtime broadcasts to all subscribers in `live_chat_{stream_id}` topic

### StreamViewerEvents
- Server stream from Runtime → Django
- Runtime emits `ViewerJoined`, `ViewerLeft`, `StreamFailed` events
- Django consumer subscribes and updates DB / emits Outbox

---

## 6. Ant Media integration

Configuration via env / secrets:
```
ANT_MEDIA_REST_URL=https://...
ANT_MEDIA_APP_NAME=live
ANT_MEDIA_WS_URL=wss://...
ANT_MEDIA_USERNAME=...
ANT_MEDIA_PASSWORD=...
```

Runtime owns all Ant Media REST calls. Django code never imports Ant Media directly (per ADR-0006).

---

## 7. Idempotency & failure modes

| Failure | Behavior |
|---|---|
| Ant Media REST unreachable | `CreateStream` returns `UNAVAILABLE`; Django creates LiveStream with `status=failed_to_provision` |
| Ant Media REST 5xx | `StartBroadcast` retries 3x with exponential backoff; on fail returns `UNAVAILABLE` |
| WebSocket subscriber disconnect | Removed from in-memory topic; viewer count decremented next cycle |
| `BroadcastChat` arrives but no subscribers | Returns `{delivered: true, recipient_count: 0}` |
| Idempotency replay | Returns cached response |

---

## 8. Viewer count

Maintained in Redis:
```
HSET live:viewers:<stream_id> <user_id> <last_heartbeat_ts>
```

Heartbeat sent by WebSocket clients every 30s. Stale entries (no heartbeat > 90s) removed by background sweep. Count = HLEN.

---

## 9. Storage

Live Runtime owns minimal state (mostly Redis-backed):
- Active WebSocket connection registry (in-process)
- Viewer presence (Redis hash)
- Stream → Ant Media session mapping (Redis kv)
- Recent moderation actions (Redis stream, 1-hour retention)

Persistent state (rooms, messages, gifts) is in **Django**, not Runtime.

---

## 10. V3 deliverables (W15-16)

- [ ] Proto + service skeleton
- [ ] gRPC server with auth + tracing
- [ ] WebSocket gateway with JWT auth
- [ ] Ant Media REST adapter (replicated from legacy)
- [ ] CreateStream / StartBroadcast / StopBroadcast
- [ ] BroadcastChat / BroadcastGift implementations
- [ ] WatchConfig endpoint
- [ ] Redis-backed viewer presence
- [ ] StreamViewerEvents bidi stream → Django
- [ ] End-to-end test: broadcaster start → mobile viewer join → chat → gift → broadcast received

---

## 11. Mobile compatibility

The viewer WebSocket frame shape matches legacy (`type: chat_message`, `gift_event`) so mobile can switch endpoints with minimal code change.

Legacy: `wss://django-backend/ws/live/<id>/chat/`
New: `wss://live-runtime.example.com/ws/v1/live/<id>/chat`

Path slightly different; frame shapes consistent.
