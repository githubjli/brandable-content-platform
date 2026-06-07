"""Models for content.gift (gift.md §1, §3).

Cross-content gifting: a sender gifts MP/MC to the owner of a video / drama
series. Wallet movements go through apps/economy; this app owns the catalog and
the immutable GiftTransaction record. Live-stream gifting is V3.
"""

from __future__ import annotations

from django.db.models import (
    BooleanField,
    CharField,
    DecimalField,
    Index,
    PositiveIntegerField,
    TextField,
    URLField,
    UUIDField,
)

from libs.errors.base_model import AbstractBaseModel


class GiftCatalogItem(AbstractBaseModel):
    """A display gift (icon/animation + preset amount). Charge logic is amount-
    based; the catalog is a display hint only (gift.md §1)."""

    code = CharField(max_length=64, unique=True)
    name = CharField(max_length=120)
    emoji = CharField(max_length=16, blank=True)
    icon_url = URLField(blank=True)
    animation_url = URLField(blank=True)
    preset_amount = DecimalField(max_digits=18, decimal_places=4, default=0)
    preset_currency = CharField(max_length=20, default="MP")
    is_active = BooleanField(default=True)
    sort_order = PositiveIntegerField(default=0)

    class Meta:
        db_table = "content_gift_catalog_item"
        ordering = ["sort_order", "code"]

    def __str__(self) -> str:
        return f"GiftCatalogItem({self.code})"


class GiftTransaction(AbstractBaseModel):
    """An immutable record of a sent gift (gift.md §3)."""

    VIDEO = "video"
    DRAMA_SERIES = "drama_series"
    LIVE_STREAM = "live_stream"
    TARGET_TYPE = [(VIDEO, VIDEO), (DRAMA_SERIES, DRAMA_SERIES), (LIVE_STREAM, LIVE_STREAM)]

    idempotency_key = CharField(max_length=128, unique=True)
    sender_id = UUIDField(db_index=True)
    receiver_id = UUIDField(db_index=True)
    target_type = CharField(max_length=20, choices=TARGET_TYPE)
    target_id = UUIDField()
    amount = DecimalField(max_digits=18, decimal_places=4)
    currency = CharField(max_length=20)  # MP | MC
    payment_method = CharField(max_length=20)  # meow_points | meow_credit
    gift_code = TextField(blank=True)  # display hint only

    sender_wallet_ledger_id = UUIDField(null=True, blank=True)
    receiver_wallet_ledger_id = UUIDField(null=True, blank=True)

    class Meta:
        db_table = "content_gift_transaction"
        ordering = ["-created_at"]
        indexes = [
            Index(fields=["sender_id", "created_at"], name="idx_gift_sender_created"),
            Index(fields=["receiver_id", "created_at"], name="idx_gift_receiver_created"),
            Index(
                fields=["target_type", "target_id", "created_at"],
                name="idx_gift_target_created",
            ),
        ]

    def __str__(self) -> str:
        return f"GiftTransaction({self.target_type}:{self.target_id}, {self.amount}{self.currency})"
