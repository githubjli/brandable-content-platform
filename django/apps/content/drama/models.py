"""Models for content.drama (content-drama.md §1-3, §9).

Drama series + episodes with four unlock methods (free / meow_points /
meow_credit / membership). Wallet debits go through apps/economy; membership
access through apps/membership. Counts are denormalized on DramaSeries.
"""

from __future__ import annotations

from django.db.models import (
    CASCADE,
    PROTECT,
    SET_NULL,
    BooleanField,
    CharField,
    DecimalField,
    ForeignKey,
    Index,
    JSONField,
    PositiveIntegerField,
    SlugField,
    TextField,
    UniqueConstraint,
    URLField,
    UUIDField,
)

from libs.errors.base_model import AbstractBaseModel


class DramaCategory(AbstractBaseModel):
    """Drama catalog category (content-local)."""

    name = CharField(max_length=120)
    slug = SlugField(max_length=120, unique=True)
    sort_order = PositiveIntegerField(default=0)
    is_active = BooleanField(default=True)

    class Meta:
        db_table = "content_drama_category"
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return f"DramaCategory({self.slug})"


class DramaSeries(AbstractBaseModel):
    """A drama series (collection of episodes)."""

    owner_user_id = UUIDField(db_index=True)
    category = ForeignKey(
        DramaCategory, on_delete=SET_NULL, null=True, blank=True, related_name="series"
    )
    title = CharField(max_length=300)
    description = TextField(blank=True)
    cover_url = URLField(blank=True)
    tags = JSONField(default=list, blank=True)

    view_count = PositiveIntegerField(default=0)
    favorite_count = PositiveIntegerField(default=0)
    comment_count = PositiveIntegerField(default=0)
    share_count = PositiveIntegerField(default=0)
    total_episodes = PositiveIntegerField(default=0)
    free_episodes = PositiveIntegerField(default=0)

    is_active = BooleanField(default=True)

    class Meta:
        db_table = "content_drama_series"
        ordering = ["-created_at"]
        indexes = [
            Index(fields=["is_active", "created_at"], name="idx_series_active_created"),
            Index(fields=["owner_user_id", "created_at"], name="idx_series_owner_created"),
        ]

    def __str__(self) -> str:
        return f"DramaSeries({self.title})"


class DramaEpisode(AbstractBaseModel):
    """An episode within a series. `unlock_type` drives the access rules."""

    FREE = "free"
    MEOW_POINTS = "meow_points"
    MEOW_CREDIT = "meow_credit"
    MEMBERSHIP = "membership"
    UNLOCK_TYPE = [
        (FREE, FREE),
        (MEOW_POINTS, MEOW_POINTS),
        (MEOW_CREDIT, MEOW_CREDIT),
        (MEMBERSHIP, MEMBERSHIP),
    ]

    series = ForeignKey(DramaSeries, on_delete=CASCADE, related_name="episodes")
    episode_no = PositiveIntegerField()
    title = CharField(max_length=300)
    description = TextField(blank=True)
    duration_seconds = PositiveIntegerField(default=0)
    thumbnail_url = URLField(blank=True)
    is_free = BooleanField(default=False)
    unlock_type = CharField(max_length=20, choices=UNLOCK_TYPE, default=FREE)
    points_price = DecimalField(max_digits=18, decimal_places=4, default=0)
    credits_price = DecimalField(max_digits=18, decimal_places=4, default=0)
    playback_url = URLField(blank=True)
    hls_url = URLField(blank=True)
    is_active = BooleanField(default=True)

    class Meta:
        db_table = "content_drama_episode"
        ordering = ["episode_no"]
        constraints = [
            UniqueConstraint(fields=["series", "episode_no"], name="uq_episode_series_no"),
        ]

    def __str__(self) -> str:
        return f"DramaEpisode(series={self.series_id}, no={self.episode_no})"


class DramaUnlock(AbstractBaseModel):
    """A user's unlock of a paid episode — UNIQUE makes unlocking idempotent."""

    user_id = UUIDField(db_index=True)
    episode = ForeignKey(DramaEpisode, on_delete=PROTECT, related_name="unlocks")
    unlock_type = CharField(max_length=20)  # meow_points | meow_credit
    ledger_entry_id = UUIDField(null=True, blank=True)

    class Meta:
        db_table = "content_drama_unlock"
        ordering = ["-created_at"]
        constraints = [
            UniqueConstraint(fields=["user_id", "episode"], name="uq_unlock_user_episode"),
        ]

    def __str__(self) -> str:
        return f"DramaUnlock(user={self.user_id}, episode={self.episode_id})"
