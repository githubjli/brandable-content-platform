"""Service layer for membership (membership.md §0, §3).

The entitlement read path (get_active_membership) is the boundary other apps use;
grant_membership is the single write path that flips entitlement state. V1 is the
internal foundation — the user-facing HTTP endpoints are V2.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from libs.errors.exceptions import NotFoundError

from .models import MembershipPlan, UserMembership

logger = logging.getLogger(__name__)


def _emit(
    event_type: str, payload: dict, idempotency_key: str, actor_id: str | None = None
) -> None:
    try:
        from apps.events.services import emit

        emit(
            event_type=event_type,
            idempotency_key=idempotency_key,
            payload=payload,
            actor_id=actor_id,
        )
    except Exception:
        logger.debug("_emit: emit failed; skipping %s", event_type)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def get_active_membership(*, user_id: str) -> UserMembership | None:
    """Return the user's current active (non-expired) membership, or None."""
    now = timezone.now()
    membership = (
        UserMembership.objects.select_related("plan")
        .filter(user_id=user_id, status=UserMembership.ACTIVE)
        .order_by("-ends_at")
        .first()
    )
    if membership is None:
        return None
    if membership.is_expired(now=now):
        return None
    return membership


def has_active_membership(*, user_id: str) -> bool:
    return get_active_membership(user_id=user_id) is not None


def serialize_membership(membership: UserMembership | None) -> dict[str, Any]:
    """Public /membership/me shape (the V2 endpoint will reuse this)."""
    if membership is None:
        return {"active_membership": None}
    now = timezone.now()
    days_remaining = None
    if membership.ends_at is not None:
        days_remaining = max(0, (membership.ends_at - now).days)
    return {
        "user_id": str(membership.user_id),
        "plan": {
            "id": str(membership.plan_id),
            "code": membership.plan.code,
            "name": membership.plan.name,
        },
        "status": membership.status,
        "starts_at": _iso(membership.starts_at),
        "ends_at": _iso(membership.ends_at),
        "is_expired": membership.is_expired(now=now),
        "days_remaining": days_remaining,
        "auto_renew": membership.auto_renew,
        "subscription_id": str(membership.subscription_id) if membership.subscription_id else None,
    }


def grant_membership(
    *,
    user_id: str,
    plan_code: str,
    duration_days: int | None = None,
    source: str = "purchase",
    idempotency_key: str,
) -> UserMembership:
    """Grant (or extend) a membership. Expires any existing active row so a user
    holds at most one active membership. Emits membership.MembershipGranted."""
    try:
        plan = MembershipPlan.objects.get(code=plan_code, is_active=True)
    except MembershipPlan.DoesNotExist:
        raise NotFoundError(code="PLAN_NOT_FOUND", message=f"No active plan '{plan_code}'.")

    now = datetime.now(tz=UTC)
    days = duration_days if duration_days is not None else plan.duration_days
    ends_at = now + timedelta(days=days) if days else None

    with transaction.atomic():
        UserMembership.objects.filter(user_id=user_id, status=UserMembership.ACTIVE).update(
            status=UserMembership.EXPIRED
        )
        membership = UserMembership.objects.create(
            user_id=user_id,
            plan=plan,
            status=UserMembership.ACTIVE,
            starts_at=now,
            ends_at=ends_at,
            source=source,
        )
        _emit(
            event_type="membership.MembershipGranted",
            payload={
                "user_id": str(user_id),
                "membership_id": str(membership.id),
                "plan_code": plan.code,
                "ends_at": _iso(ends_at),
                "occurred_at": _iso(now),
            },
            idempotency_key=idempotency_key,
            actor_id=str(user_id),
        )
    return membership
