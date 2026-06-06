"""Models for membership (membership.md §0).

Membership is a Django app (no gRPC service). It owns the user entitlement state
(`UserMembership`); grants/revocations happen in Django transactions and are the
source of truth. V1 is the internal foundation + active-membership import; the
user-facing purchase/subscription endpoints are V2.
"""

from __future__ import annotations

from datetime import datetime

from django.db.models import (
    PROTECT,
    BooleanField,
    CharField,
    DateTimeField,
    DecimalField,
    ForeignKey,
    Index,
    PositiveIntegerField,
    UUIDField,
)
from django.utils import timezone

from libs.errors.base_model import AbstractBaseModel


class MembershipPlan(AbstractBaseModel):
    """A purchasable membership tier. Admin-/import-seeded in V1."""

    code = CharField(max_length=64, unique=True)
    name = CharField(max_length=200)
    duration_days = PositiveIntegerField(default=30)
    price_amount = DecimalField(max_digits=18, decimal_places=4, default=0)
    price_currency = CharField(max_length=20, default="USD")
    is_active = BooleanField(default=True)

    class Meta:
        db_table = "membership_plan"
        ordering = ["code"]

    def __str__(self) -> str:
        return f"MembershipPlan({self.code})"


class UserMembership(AbstractBaseModel):
    """A user's entitlement record. One active row per user at a time."""

    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    STATUS = [(ACTIVE, ACTIVE), (EXPIRED, EXPIRED), (CANCELLED, CANCELLED)]

    user_id = UUIDField(db_index=True)
    plan = ForeignKey(MembershipPlan, on_delete=PROTECT, related_name="memberships")
    status = CharField(max_length=20, choices=STATUS, default=ACTIVE)
    starts_at = DateTimeField(default=timezone.now)
    ends_at = DateTimeField(null=True, blank=True)  # null = lifetime
    auto_renew = BooleanField(default=False)
    subscription_id = UUIDField(null=True, blank=True)
    source = CharField(max_length=20, default="purchase")  # purchase | admin | migration
    source_ref = CharField(max_length=128, blank=True, default="")  # legacy id (import idempotency)

    class Meta:
        db_table = "membership_user_membership"
        ordering = ["-created_at"]
        indexes = [
            Index(fields=["user_id", "status"], name="idx_membership_user_status"),
        ]

    def is_expired(self, *, now: datetime | None = None) -> bool:
        if self.ends_at is None:
            return False
        return self.ends_at <= (now or timezone.now())

    def __str__(self) -> str:
        return f"UserMembership(user={self.user_id}, {self.status})"
