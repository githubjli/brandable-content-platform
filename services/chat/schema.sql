-- Chat service Postgres schema (chat.md §6). The Chat service owns this store and
-- never reads Django's DB (ADR-0006); it persists user IDs as the durable
-- identity reference. Applied by the service's own migration tooling (not Django).

CREATE TABLE IF NOT EXISTS room (
  id UUID PRIMARY KEY,
  type TEXT NOT NULL,                -- 'direct' | 'group'
  created_by_user_id UUID NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  last_message_at TIMESTAMPTZ,
  metadata JSONB
);

CREATE TABLE IF NOT EXISTS room_member (
  room_id UUID REFERENCES room(id),
  user_id UUID NOT NULL,
  joined_at TIMESTAMPTZ NOT NULL,
  last_read_message_id UUID,
  PRIMARY KEY (room_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_room_member_user ON room_member(user_id);

CREATE TABLE IF NOT EXISTS message (
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

CREATE INDEX IF NOT EXISTS idx_message_room_created ON message(room_id, created_at DESC);
