"""Models for content.video (content-video.md §1-2).

Public video catalog + engagement (like / comment / share / view). Owner identity
lives in apps/identity; gifts live in apps/content (gift.md) — not here. Counts are
denormalized on Video and bumped atomically via F() on each interaction.
"""

from __future__ import annotations

from django.db.models import (
    CASCADE,
    SET_NULL,
    BooleanField,
    CharField,
    ForeignKey,
    GenericIPAddressField,
    Index,
    PositiveIntegerField,
    SlugField,
    TextField,
    UniqueConstraint,
    URLField,
    UUIDField,
)

from libs.errors.base_model import AbstractBaseModel


class VideoCategory(AbstractBaseModel):
    """Video catalog category (content-local; commerce keeps its own)."""

    name = CharField(max_length=120)
    slug = SlugField(max_length=120, unique=True)
    sort_order = PositiveIntegerField(default=0)
    is_active = BooleanField(default=True)

    class Meta:
        db_table = "content_video_category"
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return f"VideoCategory({self.slug})"


class Video(AbstractBaseModel):
    """A creator-uploaded video. File hosting/transcoding is out of V2 scope —
    `file_url` / `thumbnail_url` are stored as provided."""

    PUBLIC = "public"
    PRIVATE = "private"
    UNLISTED = "unlisted"
    VISIBILITY = [(PUBLIC, PUBLIC), (PRIVATE, PRIVATE), (UNLISTED, UNLISTED)]

    FREE = "free"
    MEMBERS_ONLY = "members_only"
    ACCESS_TYPE = [(FREE, FREE), (MEMBERS_ONLY, MEMBERS_ONLY)]

    owner_user_id = UUIDField(db_index=True)
    category = ForeignKey(
        VideoCategory, on_delete=SET_NULL, null=True, blank=True, related_name="videos"
    )
    title = CharField(max_length=300)
    description = TextField(blank=True)
    visibility = CharField(max_length=20, choices=VISIBILITY, default=PUBLIC)
    access_type = CharField(max_length=20, choices=ACCESS_TYPE, default=FREE)
    file_url = URLField(blank=True)
    thumbnail_url = URLField(blank=True)
    duration_seconds = PositiveIntegerField(default=0)
    preview_seconds = PositiveIntegerField(default=0)

    view_count = PositiveIntegerField(default=0)
    like_count = PositiveIntegerField(default=0)
    comment_count = PositiveIntegerField(default=0)
    share_count = PositiveIntegerField(default=0)

    is_active = BooleanField(default=True)

    class Meta:
        db_table = "content_video"
        ordering = ["-created_at"]
        indexes = [
            Index(fields=["visibility", "is_active", "created_at"], name="idx_video_vis_created"),
            Index(fields=["owner_user_id", "created_at"], name="idx_video_owner_created"),
        ]

    def __str__(self) -> str:
        return f"Video({self.title})"


class VideoLike(AbstractBaseModel):
    """One like per (user, video) — UNIQUE makes liking idempotent."""

    video = ForeignKey(Video, on_delete=CASCADE, related_name="likes")
    user_id = UUIDField(db_index=True)

    class Meta:
        db_table = "content_video_like"
        constraints = [
            UniqueConstraint(fields=["video", "user_id"], name="uq_videolike_video_user"),
        ]

    def __str__(self) -> str:
        return f"VideoLike(video={self.video_id}, user={self.user_id})"


class VideoComment(AbstractBaseModel):
    """Threaded one level deep: top-level comments + replies (parent set)."""

    video = ForeignKey(Video, on_delete=CASCADE, related_name="comments")
    user_id = UUIDField(db_index=True)
    parent = ForeignKey("self", on_delete=CASCADE, null=True, blank=True, related_name="replies")
    content = TextField()
    reply_count = PositiveIntegerField(default=0)

    class Meta:
        db_table = "content_video_comment"
        ordering = ["-created_at"]
        indexes = [
            Index(fields=["video", "parent", "created_at"], name="idx_vcomment_video_parent"),
        ]

    def __str__(self) -> str:
        return f"VideoComment(video={self.video_id})"


class VideoShare(AbstractBaseModel):
    """Each share is tracked separately (analytics); anonymous shares allowed."""

    video = ForeignKey(Video, on_delete=CASCADE, related_name="shares")
    user_id = UUIDField(null=True, blank=True, db_index=True)
    channel = CharField(max_length=64, blank=True)
    ip_address = GenericIPAddressField(null=True, blank=True)
    user_agent = CharField(max_length=400, blank=True)

    class Meta:
        db_table = "content_video_share"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"VideoShare(video={self.video_id})"


class VideoView(AbstractBaseModel):
    """A view event. Deduplicated per user/IP per minute via dedup_key UNIQUE."""

    video = ForeignKey(Video, on_delete=CASCADE, related_name="views")
    user_id = UUIDField(null=True, blank=True, db_index=True)
    ip_address = GenericIPAddressField(null=True, blank=True)
    dedup_key = CharField(max_length=200)

    class Meta:
        db_table = "content_video_view"
        ordering = ["-created_at"]
        constraints = [
            UniqueConstraint(fields=["dedup_key"], name="uq_videoview_dedup"),
        ]

    def __str__(self) -> str:
        return f"VideoView(video={self.video_id})"
