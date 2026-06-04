# ChatService gRPC Contract

**Second gRPC service** (V2 at W12-13). Handles direct messages, group chats, message history.

**Service location**: `services/chat/`
**Implementation**: Python (grpcio + asyncio) in V1; Go rewrite candidate as load grows
**Proto package**: `chat.v1`

> **Note**: Live stream chat is **separate** — see live-runtime.md. This service handles 1:1 DMs and persistent group chats.

---

## 1. Proto definition

`proto/chat/v1/chat.proto`:

```proto
syntax = "proto3";

package chat.v1;

import "common/v1/common.proto";
import "google/protobuf/timestamp.proto";

service ChatService {
  rpc Ping(PingRequest) returns (PingResponse);

  // --- Rooms ---
  rpc CreateDirectRoom(CreateDirectRoomRequest) returns (Room) {
    option (idempotency.level) = IDEMPOTENT;
  }
  rpc GetRoom(GetRoomRequest) returns (Room);
  rpc ListUserRooms(ListUserRoomsRequest) returns (ListUserRoomsResponse);

  // --- Messages ---
  rpc SendMessage(SendMessageRequest) returns (Message) {
    option (idempotency.level) = IDEMPOTENT;
  }
  rpc ListMessages(ListMessagesRequest) returns (ListMessagesResponse);

  // --- Real-time stream (bidi) ---
  rpc Subscribe(stream SubscribeRequest) returns (stream RoomEvent);

  // --- Mark read ---
  rpc MarkRead(MarkReadRequest) returns (MarkReadResponse);
}

// --- Room ---

message Room {
  string room_id = 1;
  RoomType type = 2;
  repeated string member_user_ids = 3;
  string created_by_user_id = 4;
  google.protobuf.Timestamp created_at = 5;
  google.protobuf.Timestamp last_message_at = 6;
  Message last_message = 7;     // preview
  int32 unread_count = 8;        // for the calling user
}

enum RoomType {
  ROOM_TYPE_UNSPECIFIED = 0;
  ROOM_TYPE_DIRECT = 1;        // 1:1
  ROOM_TYPE_GROUP = 2;          // 3+ members (V3)
}

message CreateDirectRoomRequest {
  string idempotency_key = 1;
  string other_user_id = 2;
  string trace_id = 3;
}

message GetRoomRequest {
  string room_id = 1;
}

message ListUserRoomsRequest {
  string user_id = 1;        // caller user_id (verified via JWT)
  int32 limit = 2;
  string cursor = 3;
}

message ListUserRoomsResponse {
  repeated Room results = 1;
  string next_cursor = 2;
}

// --- Message ---

message Message {
  string message_id = 1;
  string room_id = 2;
  string sender_user_id = 3;
  MessageType type = 4;
  string content = 5;
  bytes attachment_payload = 6;  // JSON, varies by type
  google.protobuf.Timestamp created_at = 7;
  bool is_deleted = 8;
}

enum MessageType {
  MESSAGE_TYPE_UNSPECIFIED = 0;
  MESSAGE_TYPE_TEXT = 1;
  MESSAGE_TYPE_IMAGE = 2;       // V3
  MESSAGE_TYPE_PRODUCT_SHARE = 3;
  MESSAGE_TYPE_SYSTEM = 4;       // join/leave notifications
}

message SendMessageRequest {
  string idempotency_key = 1;
  string room_id = 2;
  MessageType type = 3;
  string content = 4;
  bytes attachment_payload = 5;
  string trace_id = 6;
}

message ListMessagesRequest {
  string room_id = 1;
  int32 limit = 2;          // max 100
  string before_message_id = 3;  // for cursor backwards (history scroll)
}

message ListMessagesResponse {
  repeated Message results = 1;
  string next_before_message_id = 2;  // null if at history start
}

// --- Subscribe (bidi stream) ---

message SubscribeRequest {
  oneof action {
    SubscribeRooms subscribe_rooms = 1;
    UnsubscribeRoom unsubscribe_room = 2;
    SendInline send_inline = 3;
  }
}

message SubscribeRooms {
  repeated string room_ids = 1;
}

message UnsubscribeRoom {
  string room_id = 1;
}

message SendInline {
  string idempotency_key = 1;
  string room_id = 2;
  MessageType type = 3;
  string content = 4;
}

message RoomEvent {
  string room_id = 1;
  oneof event {
    Message message_created = 2;
    Message message_deleted = 3;
    UserTyping user_typing = 4;
    UserPresence user_presence = 5;
  }
  google.protobuf.Timestamp event_time = 6;
}

message UserTyping {
  string user_id = 1;
  bool is_typing = 2;
}

message UserPresence {
  string user_id = 1;
  bool is_online = 2;
}

// --- Mark read ---

message MarkReadRequest {
  string room_id = 1;
  string up_to_message_id = 2;
}

message MarkReadResponse {
  int32 new_unread_count = 1;
}

// --- Health ---

message PingRequest {}
message PingResponse {
  string version = 1;
  google.protobuf.Timestamp server_time = 2;
}
```

---

## 2. RPC behavior

### CreateDirectRoom
- Idempotent: same idempotency_key returns same room
- If room with same two members exists, returns that room (not duplicate)
- Errors: `INVALID_ARGUMENT` (self-room), `NOT_FOUND` (other_user_id)

### SendMessage
- Idempotent
- Returns the persisted Message
- Broadcasts to all `Subscribe` connections for the room
- Errors: `PERMISSION_DENIED` (not room member), `NOT_FOUND` (room)

### Subscribe (bidi stream)
- Client opens long-lived stream
- Sends `SubscribeRooms` action with room ids to monitor
- Receives `RoomEvent` for any room subscribed
- Client can also send messages inline via `SendInline` action (avoids opening separate RPC for low-latency UX)
- Server keeps connection alive via periodic keepalive
- Stream closed on auth failure, network drop, or explicit client close

### ListMessages
- History scroll: pass `before_message_id` to get older messages
- Limit max 100
- Returns deleted messages with `is_deleted=true` (clients should hide content)

### MarkRead
- Sets read pointer for the calling user in the room
- Returns updated unread_count

---

## 3. Mobile WebSocket gateway

mobile clients connect via WebSocket (Chat service exposes it). Internally, the gateway calls `Subscribe` bidi stream to the service.

```
mobile ←─ wss://chat.example.com/ws/v1/chat ─→ ChatService Subscribe RPC
       │
       └─ Auth: Bearer <jwt> in connection query string
```

WebSocket frame envelope (mobile↔gateway):
```json
{ "type": "subscribe_rooms", "data": { "room_ids": ["..."] } }
{ "type": "send", "data": { "idempotency_key": "...", "room_id": "...", "content": "...", "type": "text" } }
```

Inbound events:
```json
{ "type": "message_created", "data": { "message_id": "...", "room_id": "...", "sender_user_id": "...", "content": "...", "created_at": "..." } }
{ "type": "user_typing", "data": { "room_id": "...", "user_id": "...", "is_typing": true } }
```

---

## 4. Architecture

```
mobile WebSocket
        │
        ▼
ChatService gateway (Python, asyncio)
   │ │
   │ └─ Bidi stream open
   │
   ▼
Chat business logic (Python)
   │
   ├─→ PostgreSQL (rooms, messages, read pointers)
   ├─→ Redis (presence, typing, ephemeral)
   └─→ JWT validation via JWKS
```

V2 may move presence/typing to a dedicated pub/sub channel.

---

## 5. Authentication

- WebSocket: `Authorization: Bearer <user_jwt>` in connection query
- gRPC (internal): service-to-service JWT
- User can only access rooms they're a member of

---

## 6. Data model

```sql
CREATE TABLE room (
  id UUID PRIMARY KEY,
  type TEXT NOT NULL,                -- 'direct' | 'group'
  created_by_user_id UUID NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  last_message_at TIMESTAMPTZ,
  metadata JSONB
);

CREATE TABLE room_member (
  room_id UUID REFERENCES room(id),
  user_id UUID NOT NULL,
  joined_at TIMESTAMPTZ NOT NULL,
  last_read_message_id UUID,
  PRIMARY KEY (room_id, user_id)
);

CREATE INDEX idx_room_member_user ON room_member(user_id);

CREATE TABLE message (
  id UUID PRIMARY KEY,
  idempotency_key TEXT UNIQUE NOT NULL,
  room_id UUID REFERENCES room(id),
  sender_user_id UUID NOT NULL,
  type TEXT NOT NULL,
  content TEXT,
  attachment_payload JSONB,
  is_deleted BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_message_room_created ON message(room_id, created_at DESC);
```

---

## 7. Cross-domain integration

ChatService **does not** read Django's database (per ADR-0006). Where it needs user info:

- User display name / avatar: cached at first contact; refreshed via gRPC call to Identity service or via subscription to `identity.ProfileUpdated` Outbox events
- Block lists: per-user JSON cache from Identity service

---

## 8. V2 deliverables (W12-13)

- [ ] Proto + service skeleton
- [ ] PostgreSQL schema
- [ ] gRPC server with auth + tracing
- [ ] WebSocket gateway
- [ ] CreateDirectRoom + SendMessage + ListMessages
- [ ] Subscribe bidi stream
- [ ] MarkRead + unread counts
- [ ] Mobile contract test
- [ ] systemd unit + nginx WS config (sticky sessions or stateless via load balancer)
- [ ] Observability (metrics: `chat_active_streams`, `chat_message_throughput`, `chat_p99_latency_ms`)

---

## 9. V3 features (deferred)

- Group chat (3+ members, invites, kicks)
- Image / file attachments
- Voice messages
- Reactions
- Edit messages
- Server-side moderation
- Encryption at rest / E2E
