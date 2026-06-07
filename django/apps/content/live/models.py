"""Models for content.live (content-live.md §1, §5, §7).

Django owns live-stream metadata + the lifecycle state machine. The realtime
plane (Ant Media, WebSocket, broadcast) lives in services/live_runtime and is
reached through a runtime adapter; the credentials it returns are cached here.
"""

from __future__ import annotations

from django.db.models import (
    SET_NULL,
    BooleanField,
    CharField,
    DateTimeField,
    ForeignKey,
    Index,
    JSONField,
    PositiveIntegerField,
    SlugField,
    TextField,
    URLField,
    UUIDField,
)

from libs.errors.base_model import AbstractBaseModel


class LiveCategory(AbstractBaseModel):
    """Live catalog category (content-local)."""

    name = CharField(max_length=120)
    slug = SlugField(max_length=120, unique=True)
    sort_order = PositiveIntegerField(default=0)
    is_active = BooleanField(default=True)

    class Meta:
        db_table = "content_live_category"
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return f"LiveCategory({self.slug})"


class LiveStream(AbstractBaseModel):
    """A live stream + its lifecycle state (content-live.md §7).

    IDLE ─(prepare)→ READY ─(start)→ LIVE ─(end)→ ENDED ; any ─→ FAILED.
    """

    IDLE = "idle"
    READY = "ready"
    LIVE = "live"
    ENDED = "ended"
    FAILED = "failed"
    STATUS = [
        (IDLE, IDLE),
        (READY, READY),
        (LIVE, LIVE),
        (ENDED, ENDED),
        (FAILED, FAILED),
    ]

    PUBLIC = "public"
    PRIVATE = "private"
    VISIBILITY = [(PUBLIC, PUBLIC), (PRIVATE, PRIVATE)]

    owner_user_id = UUIDField(db_index=True)
    category = ForeignKey(
        LiveCategory, on_delete=SET_NULL, null=True, blank=True, related_name="streams"
    )
    title = CharField(max_length=300)
    description = TextField(blank=True)
    visibility = CharField(max_length=20, choices=VISIBILITY, default=PUBLIC)
    status = CharField(max_length=20, choices=STATUS, default=IDLE)

    thumbnail_url = URLField(blank=True)
    preview_image_url = URLField(blank=True)
    snapshot_url = URLField(blank=True)
    viewer_count = PositiveIntegerField(default=0)

    # Realtime credentials from the live-runtime CreateStream call.
    ant_media_stream_id = CharField(max_length=128, blank=True)
    stream_key = CharField(max_length=255, blank=True)
    rtmp_url = URLField(blank=True)
    hls_url = URLField(blank=True)
    websocket_url = URLField(blank=True)
    webrtc_publish_config = JSONField(default=dict, blank=True)

    started_at = DateTimeField(null=True, blank=True)
    ended_at = DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "content_live_stream"
        ordering = ["-created_at"]
        indexes = [
            Index(fields=["visibility", "status", "created_at"], name="idx_stream_vis_status"),
            Index(fields=["owner_user_id", "created_at"], name="idx_stream_owner_created"),
        ]

    def __str__(self) -> str:
        return f"LiveStream({self.title}, {self.status})"
